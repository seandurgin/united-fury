#!/usr/bin/env python3
"""Tiny webhook receiver for iOS Shortcut location pings.
Listens on 127.0.0.1:8888, fronted by nginx at /webhooks/location.
Stores pings in scheduled_tasks-adjacent location_history table.
"""
import json, logging, os, sqlite3, threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger("clawdia.location")

# Known places: snap to friendly name when a ping is within `radius_m` of these
# coordinates. Avoids Nominatim drift (e.g. snapping to "117 Cool Springs Drive"
# instead of Sean's actual address at 113). Add new entries as Sean acquires them.
KNOWN_PLACES = [
    {
        "name": "Home",
        "address": "113 Cool Springs Rd, North East, MD 21901",
        "lat": 39.582530,
        "lon": -75.979146,
        "radius_m": 150,
    },
    # Future: Sterling VA work site, etc.
]

def _haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters between two lat/lon points."""
    import math
    R = 6371000.0  # Earth radius in meters
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))

def match_known_place(lat, lon):
    """Return the closest known place within its radius, or None."""
    best = None
    best_dist = None
    for place in KNOWN_PLACES:
        d = _haversine_m(lat, lon, place["lat"], place["lon"])
        if d <= place["radius_m"] and (best_dist is None or d < best_dist):
            best = place
            best_dist = d
    return (best, best_dist) if best else (None, None)

def location_init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS location_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recorded_at TEXT NOT NULL,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        accuracy_m REAL,
        source TEXT,
        battery_pct INTEGER,
        raw_json TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_location_recorded ON location_history(recorded_at DESC)")
    conn.commit()

def make_handler(get_conn, secret):
    class LocationHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.info("%s - %s", self.address_string(), fmt % args)
        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            if self.path not in ("/webhooks/location", "/location"):
                self._json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8192:
                self._json(400, {"error": "bad content-length"})
                return
            try:
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
            except Exception as e:
                self._json(400, {"error": f"bad json: {e}"})
                return
            posted_secret = data.get("secret") or self.headers.get("X-Webhook-Secret", "")
            if not secret or posted_secret != secret:
                log.warning("location webhook rejected: bad secret from %s", self.client_address)
                self._json(401, {"error": "unauthorized"})
                return
            try:
                lat = float(data["lat"])
                lon = float(data["lon"])
            except (KeyError, TypeError, ValueError) as e:
                self._json(400, {"error": f"missing/invalid lat/lon: {e}"})
                return
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                self._json(400, {"error": "lat/lon out of range"})
                return
            accuracy = data.get("accuracy")
            try:
                accuracy = float(accuracy) if accuracy is not None else None
            except (TypeError, ValueError):
                accuracy = None
            battery = data.get("battery")
            try:
                battery = int(battery) if battery is not None else None
            except (TypeError, ValueError):
                battery = None
            source = str(data.get("source", "ios-shortcut"))[:64]
            recorded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                with get_conn() as conn:
                    location_init(conn)
                    conn.execute(
                        "INSERT INTO location_history (recorded_at, lat, lon, accuracy_m, source, battery_pct, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (recorded_at, lat, lon, accuracy, source, battery, body),
                    )
                    conn.commit()
            except Exception as e:
                log.error("location webhook DB error: %s", e)
                self._json(500, {"error": "storage failure"})
                return
            self._json(200, {"ok": True, "recorded_at": recorded_at})
        def do_GET(self):
            # Health check only
            if self.path in ("/webhooks/location/health", "/location/health"):
                self._json(200, {"ok": True, "service": "clawdia-location"})
            else:
                self._json(404, {"error": "POST only"})
    return LocationHandler

def start_location_server(get_conn, secret, port=8888, host="127.0.0.1"):
    handler = make_handler(get_conn, secret)
    server = HTTPServer((host, port), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="location-webhook")
    t.start()
    log.info("Location webhook server listening on %s:%d", host, port)
    return server