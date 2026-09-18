import json
import urllib.request
import urllib.error
import datetime

STATES_URL = (
    "https://opensky-network.org/api/states/all"
    # Roughly double the tight metro box so inbound/outbound aircraft show up
    # while they're still on approach/departure corridors, not only once they're
    # already directly over the city.
    "?lamin=52.10&lomin=12.70&lamax=52.95&lomax=14.10"
)

HEADERS = {
    # OpenSky's usage policy asks for a clear, unique User-Agent.
    "User-Agent": "BerlinSkywatch/1.0 (+https://github.com/yoseyo/berlin-skywatch)"
}

# adsbdb.com is a free, keyless, open route-lookup API built for ADS-B hobbyist
# projects exactly like this one — unlike OpenSky's own /flights endpoint, it
# doesn't require a registered account. Cached per callsign so we're not
# re-querying it for the same aircraft on every 5-minute poll.
ROUTE_CACHE_PATH = "data/route_cache.json"
ROUTE_CACHE_TTL_HOURS = 12  # a flight's route doesn't change mid-flight

# A plane can legitimately do 200-280 km/h for the first 20-40s *after* touchdown,
# before braking — so a single low-altitude+low-speed reading can't reliably tell
# "about to land" from "just landed", and a speed-only backstop misses the latter.
# Instead, track altitude across polls: seen low once is given the benefit of the
# doubt (could be genuine final approach), but still low a full poll cycle later is
# a much stronger signal of being on the ground, regardless of that poll's speed.
GROUND_WATCH_PATH = "data/ground_watch.json"
LOW_ALT_KM = 0.15


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def parse_iso(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "")).replace(tzinfo=None)


def fetch_states():
    req = urllib.request.Request(STATES_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return (json.load(resp).get("states")) or []


def fetch_route(callsign):
    url = "https://api.adsbdb.com/v0/callsign/" + callsign
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # unknown callsign (often a private/GA aircraft) — not an error
        raise
    route = (data.get("response") or {}).get("flightroute")
    if not route:
        return None
    origin = route.get("origin") or {}
    dest = route.get("destination") or {}
    airline = route.get("airline") or {}
    return {
        "fromCity": origin.get("municipality"),
        "fromIata": origin.get("iata_code"),
        "toCity": dest.get("municipality"),
        "toIata": dest.get("iata_code"),
        "airlineName": airline.get("name"),
        "airlineCountry": airline.get("country"),
    }


def main():
    now = datetime.datetime.utcnow()

    try:
        states = fetch_states()
    except Exception:
        return  # leave data/latest.json (and caches) untouched if OpenSky is unreachable

    flights = []
    for s in states:
        if not (s[8] is False and s[1] and s[1].strip()):
            continue
        alt_km = (s[13] or s[7] or 0) / 1000
        speed_kmh = (s[9] or 0) * 3.6
        # OpenSky's on_ground flag is known to lag right at touchdown — a plane can
        # sit on the runway at near-zero altitude/speed for a bit while still flagged
        # airborne. Back it up with a physical check so landed aircraft don't linger.
        if alt_km < 0.1 and speed_kmh < 80:
            continue  # obviously landed/taxiing — clearly low and clearly slow
        flights.append({
            "callsign": s[1].strip(),
            "country": s[2] or "—",
            "lat": s[6],
            "lon": s[5],
            "altKm": round(alt_km, 1),
            "speedKmh": round(speed_kmh),
            "heading": round(s[10] or 0),
            "vertRateMs": round(s[11], 1) if s[11] is not None else 0,
        })

    ground_watch = load_json(GROUND_WATCH_PATH, {})
    still_airborne = []
    for f in flights:
        cs = f["callsign"]
        if f["altKm"] < LOW_ALT_KM:
            if cs in ground_watch:
                continue  # low on a previous poll too — treat as landed regardless of speed
            ground_watch[cs] = now.isoformat() + "Z"  # first low reading — show it, but flag it
        else:
            ground_watch.pop(cs, None)  # back at altitude — clear any stale low-altitude flag
        still_airborne.append(f)
    flights = still_airborne

    cutoff = now - datetime.timedelta(hours=2)
    ground_watch = {
        k: v for k, v in ground_watch.items() if parse_iso(v) > cutoff
    }

    route_cache = load_json(ROUTE_CACHE_PATH, {})

    for f in flights:
        cs = f["callsign"]
        cached = route_cache.get(cs)
        fresh = cached and (now - parse_iso(cached["checked_at"])).total_seconds() < ROUTE_CACHE_TTL_HOURS * 3600

        if not fresh:
            try:
                route = fetch_route(cs)
                route_cache[cs] = {"route": route, "checked_at": now.isoformat() + "Z"}
                cached = route_cache[cs]
            except Exception:
                pass  # transient failure — leave any existing cache entry as-is, retry next poll

        route = cached["route"] if cached else None
        if route:
            f["fromCity"] = route.get("fromCity")
            f["fromIata"] = route.get("fromIata")
            f["toCity"] = route.get("toCity")
            f["toIata"] = route.get("toIata")
            if route.get("airlineName"):
                f["airline"] = route["airlineName"]
                f["airlineCountry"] = route.get("airlineCountry")

    # Prune cache entries older than 48h so the file doesn't grow unbounded.
    cutoff = now - datetime.timedelta(hours=48)
    route_cache = {
        k: v for k, v in route_cache.items()
        if parse_iso(v["checked_at"]) > cutoff
    }

    with open("data/latest.json", "w") as out:
        json.dump({
            "flights": flights,
            "updated_at": now.isoformat() + "Z"
        }, out, indent=2)

    with open(ROUTE_CACHE_PATH, "w") as f:
        json.dump(route_cache, f, indent=2)

    with open(GROUND_WATCH_PATH, "w") as f:
        json.dump(ground_watch, f, indent=2)


main()
