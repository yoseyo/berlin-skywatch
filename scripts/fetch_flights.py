import json
import urllib.request
import datetime

URL = (
    "https://opensky-network.org/api/states/all"
    # Roughly double the tight metro box so inbound/outbound aircraft show up
    # while they're still on approach/departure corridors, not only once they're
    # already directly over the city.
    "?lamin=52.10&lomin=12.70&lamax=52.95&lomax=14.10"
)

req = urllib.request.Request(URL, headers={
    # OpenSky's usage policy asks for a clear, unique User-Agent.
    "User-Agent": "BerlinSkywatch/1.0 (+https://github.com/yoseyo/berlin-skywatch)"
})

states = None
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        states = (json.load(resp).get("states")) or []
except Exception:
    pass  # leave the existing data/latest.json untouched if OpenSky is unreachable

if states is not None:
    flights = []
    for s in states:
        if s[8] is False and s[1] and s[1].strip():
            flights.append({
                "callsign": s[1].strip(),
                "country": s[2] or "—",
                "lat": s[6],
                "lon": s[5],
                "altKm": round((s[13] or s[7] or 0) / 1000, 1),
                "speedKmh": round((s[9] or 0) * 3.6),
                "heading": round(s[10] or 0),
            })
    with open("data/latest.json", "w") as f:
        json.dump({
            "flights": flights,
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z"
        }, f, indent=2)
