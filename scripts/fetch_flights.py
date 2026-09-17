import json
import urllib.request
import datetime

URL = (
    "https://opensky-network.org/api/states/all"
    "?lamin=52.30&lomin=13.00&lamax=52.70&lomax=13.75"
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
