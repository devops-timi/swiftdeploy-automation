# app/main.py
# Python HTTP API service for SwiftDeploy.
# Now includes /metrics endpoint in Prometheus text format.

import os
import time
import random
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

# ─────────────────────────────────────────────
# Configuration from environment variables
# ─────────────────────────────────────────────
MODE        = os.environ.get("MODE", "stable")
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
APP_PORT    = int(os.environ.get("APP_PORT", "3000"))

START_TIME = time.time()

# ─────────────────────────────────────────────
# Metrics State
# All counters and histograms tracked here.
# Protected by a lock so concurrent requests don't corrupt counts.
# ─────────────────────────────────────────────
metrics_lock = threading.Lock()

# http_requests_total — counts every request by method, path, status code
# Structure: { (method, path, status_code): count }
request_counts = {}

# http_request_duration_seconds — tracks how long each request took
# We use standard Prometheus histogram buckets
BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]

# Structure: { (method, path): {"buckets": {le: count}, "sum": float, "count": int} }
request_durations = {}


def record_request(method, path, status_code, duration):
    """
    Called after every request completes.
    Updates request_counts and request_durations.
    """
    with metrics_lock:
        # Update request counter
        key = (method, path, str(status_code))
        request_counts[key] = request_counts.get(key, 0) + 1

        # Update duration histogram
        dur_key = (method, path)
        if dur_key not in request_durations:
            # Initialize buckets — each bucket counts requests <= that duration
            request_durations[dur_key] = {
                "buckets": {str(b): 0 for b in BUCKETS},
                "sum": 0.0,
                "count": 0
            }

        d = request_durations[dur_key]
        d["sum"]   += duration
        d["count"] += 1

        # Increment all buckets where duration <= bucket value
        for b in BUCKETS:
            if duration <= b:
                d["buckets"][str(b)] += 1


def build_metrics():
    """
    Builds the Prometheus text format metrics string.
    Format: https://prometheus.io/docs/instrumenting/exposition_formats/
    Each metric has a # HELP line, # TYPE line, then data lines.
    """
    lines = []

    with metrics_lock:
        # ── http_requests_total ──────────────────────────────────
        lines.append("# HELP http_requests_total Total HTTP requests")
        lines.append("# TYPE http_requests_total counter")
        for (method, path, status), count in request_counts.items():
            lines.append(
                f'http_requests_total{{method="{method}",path="{path}",status_code="{status}"}} {count}'
            )

        # ── http_request_duration_seconds ────────────────────────
        lines.append("# HELP http_request_duration_seconds Request duration histogram")
        lines.append("# TYPE http_request_duration_seconds histogram")
        for (method, path), d in request_durations.items():
            for b in BUCKETS:
                count = d["buckets"][str(b)]
                lines.append(
                    f'http_request_duration_seconds_bucket{{method="{method}",path="{path}",le="{b}"}} {count}'
                )
            # +Inf bucket = total count (all requests fit under infinity)
            lines.append(
                f'http_request_duration_seconds_bucket{{method="{method}",path="{path}",le="+Inf"}} {d["count"]}'
            )
            lines.append(
                f'http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {d["sum"]}'
            )
            lines.append(
                f'http_request_duration_seconds_count{{method="{method}",path="{path}"}} {d["count"]}'
            )

    # ── app_uptime_seconds ───────────────────────────────────────
    uptime = time.time() - START_TIME
    lines.append("# HELP app_uptime_seconds Seconds since app started")
    lines.append("# TYPE app_uptime_seconds gauge")
    lines.append(f"app_uptime_seconds {round(uptime, 2)}")

    # ── app_mode ─────────────────────────────────────────────────
    # 0 = stable, 1 = canary
    mode_val = 1 if MODE == "canary" else 0
    lines.append("# HELP app_mode Current deployment mode (0=stable, 1=canary)")
    lines.append("# TYPE app_mode gauge")
    lines.append(f"app_mode {mode_val}")

    # ── chaos_active ─────────────────────────────────────────────
    # 0 = none, 1 = slow, 2 = error
    with chaos_lock:
        cm = chaos_state["mode"]
    chaos_val = 0 if cm is None else (1 if cm == "slow" else 2)
    lines.append("# HELP chaos_active Current chaos state (0=none, 1=slow, 2=error)")
    lines.append("# TYPE chaos_active gauge")
    lines.append(f"chaos_active {chaos_val}")

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────
# Chaos State
# ─────────────────────────────────────────────
chaos_lock  = threading.Lock()
chaos_state = {
    "mode":     None,
    "duration": 0,
    "rate":     0.0,
}


def apply_chaos():
    """
    Applies active chaos to the current request.
    Returns True if a 500 error should be sent, False otherwise.
    Never applied to /healthz or /metrics.
    """
    with chaos_lock:
        mode = chaos_state["mode"]

    if mode == "slow":
        time.sleep(chaos_state["duration"])

    elif mode == "error":
        if random.random() < chaos_state["rate"]:
            return True

    return False


class Handler(BaseHTTPRequestHandler):

    def send_json(self, status_code, data):
        """Sends a JSON response. Adds X-Mode header in canary mode."""
        body = json.dumps(data).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))

        if MODE == "canary":
            self.send_header("X-Mode", "canary")

        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Handles all GET requests."""
        start = time.time()   # Record when request started

        # ── GET /healthz — never chaos, never recorded in metrics ──
        if self.path == "/healthz":
            uptime = time.time() - START_TIME
            self.send_json(200, {"status": "ok", "uptime": round(uptime, 2)})
            return

        # ── GET /metrics — Prometheus text format ──────────────────
        # This endpoint is scraped by swiftdeploy status and OPA checks.
        # Never apply chaos here — we always need accurate metrics.
        if self.path == "/metrics":
            body = build_metrics().encode("utf-8")
            self.send_response(200)
            # Prometheus expects this specific Content-Type
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Apply chaos to all other endpoints ─────────────────────
        if apply_chaos():
            duration = time.time() - start
            record_request("GET", self.path, 500, duration)
            self.send_json(500, {"error": "Simulated server failure", "code": 500})
            return

        # ── GET / ──────────────────────────────────────────────────
        if self.path == "/":
            self.send_json(200, {
                "message": f"Welcome to SwiftDeploy! Running in {MODE} mode.",
                "mode":    MODE,
                "version": APP_VERSION,
                "time":    datetime.now(timezone.utc).isoformat(),
            })
            duration = time.time() - start
            record_request("GET", "/", 200, duration)

        else:
            self.send_json(404, {"error": "not found"})
            duration = time.time() - start
            record_request("GET", self.path, 404, duration)

    def do_POST(self):
        """Handles all POST requests."""
        start = time.time()

        if self.path == "/chaos":
            # Only available in canary mode
            if MODE != "canary":
                self.send_json(403, {"error": "chaos endpoint only available in canary mode"})
                duration = time.time() - start
                record_request("POST", "/chaos", 403, duration)
                return

            length  = int(self.headers.get("Content-Length", 0))
            body    = self.rfile.read(length)

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return

            chaos_mode = payload.get("mode")

            with chaos_lock:
                if chaos_mode == "slow":
                    chaos_state["mode"]     = "slow"
                    chaos_state["duration"] = int(payload.get("duration", 1))
                    chaos_state["rate"]     = 0.0

                elif chaos_mode == "error":
                    chaos_state["mode"]     = "error"
                    chaos_state["duration"] = 0
                    chaos_state["rate"]     = float(payload.get("rate", 0.5))

                elif chaos_mode == "recover":
                    chaos_state["mode"]     = None
                    chaos_state["duration"] = 0
                    chaos_state["rate"]     = 0.0

                else:
                    self.send_json(400, {"error": f"unknown chaos mode: {chaos_mode}"})
                    return

            self.send_json(200, {"ok": True, "chaos": chaos_state})
            duration = time.time() - start
            record_request("POST", "/chaos", 200, duration)

        else:
            self.send_json(404, {"error": "not found"})

    def log_message(self, format, *args):
        """Suppress default logs — nginx handles access logging."""
        pass


if __name__ == "__main__":
    server = HTTPServer(("", APP_PORT), Handler)
    print(f"[swiftdeploy] starting on port {APP_PORT} in {MODE} mode (v{APP_VERSION})")
    server.serve_forever()