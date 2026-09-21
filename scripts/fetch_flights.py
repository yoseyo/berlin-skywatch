import json
import math
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

# adsbdb's callsign->route mapping is a best-effort/historical association, not a
# live flight-plan lookup — airlines reuse callsigns for different routes on
# different days, so it can occasionally be stale. Cross-check it against reality:
# a plane low and near BER with neither end of its claimed route pointing at Berlin
# is almost certainly a mismatched callsign, not really flying that route right now.
BER_LAT, BER_LON = 52.3667, 13.5033
BER_PROXIMITY_KM = 20
BER_TERMINAL_ALT_KM = 3

# Aircraft type (for the heli/private-jet/airliner icon on the map) via adsbdb's
# aircraft-by-icao24 lookup. A given icao24's type never changes, so this is cached
# far longer than the route lookup — most aircraft end up permanently cached.
AIRCRAFT_CACHE_PATH = "data/aircraft_cache.json"
AIRCRAFT_CACHE_TTL_DAYS = 30

HELICOPTER_TYPES = {
    "EC35", "EC45", "EC20", "EC30", "EC55", "H125", "AS50", "AS55", "A109", "A139",
    "A169", "B06", "B407", "B429", "B412", "B212", "R22", "R44", "R66", "S76",
    "S92", "H60", "H64", "BK17", "GAZL",
}
PRIVATE_JET_TYPES = {
    # Business jets
    "GL5T", "GLEX", "GL7T", "GL6T", "CL30", "CL35", "CL60",
    "C25A", "C25B", "C25C", "C500", "C510", "C525", "C550", "C560", "C56X",
    "C650", "C680", "C700", "C750", "F2TH", "F900", "F2000", "FA6X", "FA7X", "FA8X",
    "LJ31", "LJ35", "LJ40", "LJ45", "LJ60", "LJ70", "LJ75", "PC24", "E50P", "E55P",
    "E545", "E550", "GLF4", "GLF5", "GLF6", "H25B", "HA4T", "BE40", "PRM1",
    # Small GA singles/twins/turboprops — not literally jets, but the same "small
    # private aircraft, not an airliner" bucket for icon purposes.
    "SR20", "SR22", "C172", "C182", "C206", "C210", "P28A", "P28B", "P32R", "P46T",
    "BE36", "BE58", "BE9L", "BE20", "DA40", "DA42", "DA62", "M20P", "M20T", "PC12",
}


def classify_aircraft(icao_type):
    t = (icao_type or "").strip().upper()
    if t in HELICOPTER_TYPES:
        return "heli"
    if t in PRIVATE_JET_TYPES:
        return "jet"
    return "plane"


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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


def fetch_aircraft(icao24):
    url = "https://api.adsbdb.com/v0/aircraft/" + icao24
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # unknown aircraft — not an error
        raise
    aircraft = (data.get("response") or {}).get("aircraft")
    return aircraft.get("icao_type") if aircraft else None


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
            "icao24": s[0],
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
            # The airline prefix (e.g. "EJU") reliably identifies the operator, so keep
            # it even if the specific route below gets distrusted.
            if route.get("airlineName"):
                f["airline"] = route["airlineName"]
                f["airlineCountry"] = route.get("airlineCountry")

            route_involves_ber = route.get("fromIata") == "BER" or route.get("toIata") == "BER"
            near_ber = haversine_km(f["lat"], f["lon"], BER_LAT, BER_LON) < BER_PROXIMITY_KM
            on_ber_profile = near_ber and f["altKm"] < BER_TERMINAL_ALT_KM
            if route_involves_ber or not on_ber_profile:
                f["fromCity"] = route.get("fromCity")
                f["fromIata"] = route.get("fromIata")
                f["toCity"] = route.get("toCity")
                f["toIata"] = route.get("toIata")
            # else: clearly on a Berlin approach/departure profile but the claimed route
            # doesn't involve Berlin at all — almost certainly a stale/reused-callsign
            # mismatch, so leave from/to unset rather than show a contradictory route.

    # Prune cache entries older than 48h so the file doesn't grow unbounded.
    cutoff = now - datetime.timedelta(hours=48)
    route_cache = {
        k: v for k, v in route_cache.items()
        if parse_iso(v["checked_at"]) > cutoff
    }

    aircraft_cache = load_json(AIRCRAFT_CACHE_PATH, {})

    for f in flights:
        icao24 = f.pop("icao24")
        cached = aircraft_cache.get(icao24)
        fresh = cached and (now - parse_iso(cached["checked_at"])).total_seconds() < AIRCRAFT_CACHE_TTL_DAYS * 86400

        if not fresh:
            try:
                icao_type = fetch_aircraft(icao24)
                aircraft_cache[icao24] = {"icaoType": icao_type, "checked_at": now.isoformat() + "Z"}
                cached = aircraft_cache[icao24]
            except Exception:
                pass  # transient failure — leave any existing cache entry as-is, retry next poll

        f["category"] = classify_aircraft(cached["icaoType"]) if cached else "plane"

    aircraft_cutoff = now - datetime.timedelta(days=AIRCRAFT_CACHE_TTL_DAYS * 2)
    aircraft_cache = {
        k: v for k, v in aircraft_cache.items()
        if parse_iso(v["checked_at"]) > aircraft_cutoff
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

    with open(AIRCRAFT_CACHE_PATH, "w") as f:
        json.dump(aircraft_cache, f, indent=2)


main()
