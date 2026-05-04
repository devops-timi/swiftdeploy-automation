# app/main.py
# Python HTTP API service for SwiftDeploy.
# Reads all configuration from environment variables injected by Docker Compose.

import os
import time
import random
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

# ─────────────────────────────────────────────
# Configuration from environment variables
# These are set in docker-compose.yml from manifest.yaml values
# ─────────────────────────────────────────────
MODE        = os.environ.get("MODE", "stable")        # "stable" or "canary"
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")  # Version string
APP_PORT    = int(os.environ.get("APP_PORT", "3000")) # Port to listen on

# Record server start time so we can calculate uptime
START_TIME = time.time()

# ─────────────────────────────────────────────
# Thread-safe Chaos State
# Shared across all requests — lock prevents two requests
# changing the state at the same time
# ─────────────────────────────────────────────
chaos_lock  = threading.Lock()
chaos_state = {
    "mode":     None,  # None, "slow", or "error"
    "duration": 0,     # seconds to sleep in slow mode
    "rate":     0.0,   # probability of error in error mode (0.0 to 1.0)
}


def apply_chaos():
    """
    Applies the active chaos effect to the current request.
    Called at the start of every request EXCEPT /healthz.

    - slow mode:  sleeps N seconds before the response is sent
    - error mode: randomly returns True (~rate% of the time)
                  signalling the caller to send a 500 response
    - None:       does nothing, returns False

    Returns True if the caller should send a 500 error, False otherwise.
    """
    with chaos_lock:
        # Read the current chaos mode safely inside the lock
        mode = chaos_state["mode"]

    if mode == "slow":
        # Block the request for the configured number of seconds
        time.sleep(chaos_state["duration"])

    elif mode == "error":
        # random.random() gives a float between 0.0 and 1.0
        # If it falls below the rate threshold, signal a 500 error
        if random.random() < chaos_state["rate"]:
            return True

    return False  # No error, proceed normally


class Handler(BaseHTTPRequestHandler):
    """
    Handles all incoming HTTP requests.
    Python calls do_GET() for GET requests and do_POST() for POST requests.
    """

    def send_json(self, status_code, data):
        """
        Sends a JSON response with the correct headers.
        - Always sets Content-Type: application/json
        - Always adds X-Mode: canary header when in canary mode
        """
        body = json.dumps(data).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))

        # Add X-Mode header on every response when in canary mode
        # This is forwarded to the client by nginx
        if MODE == "canary":
            self.send_header("X-Mode", "canary")

        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Handles all GET requests."""

        # ── GET /healthz ──────────────────────────────────────────
        # Health check endpoint — never apply chaos here.
        # Docker polls this every 10 seconds to check if the container
        # is healthy. If chaos affected this, Docker would kill the container.
        if self.path == "/healthz":
            uptime = time.time() - START_TIME
            self.send_json(200, {
                "status": "ok",
                "uptime": round(uptime, 2),  # seconds since server started
            })
            return

        # ── Apply chaos to all other GET endpoints ─────────────────
        # Must happen before any response is sent
        if apply_chaos():
            self.send_json(500, {"error": "Simulated server failure", "code": 500})
            return

        # ── GET / ─────────────────────────────────────────────────
        # Welcome endpoint — returns mode, version, and current timestamp
        if self.path == "/":
            self.send_json(200, {
                "message": f"Welcome to SwiftDeploy! Running in {MODE} mode.",
                "mode":    MODE,
                "version": APP_VERSION,
                "time":    datetime.now(timezone.utc).isoformat(),
            })

        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        """Handles all POST requests."""

        # ── POST /chaos ───────────────────────────────────────────
        if self.path == "/chaos":

            # Chaos endpoint is only available in canary mode
            if MODE != "canary":
                self.send_json(403, {
                    "error": "chaos endpoint only available in canary mode"
                })
                return

            # Read the request body
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)

            # Parse the JSON body
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return

            chaos_mode = payload.get("mode")

            # Update chaos state inside the lock so it's thread-safe
            with chaos_lock:
                if chaos_mode == "slow":
                    # Sleep N seconds before every subsequent response
                    chaos_state["mode"]     = "slow"
                    chaos_state["duration"] = int(payload.get("duration", 1))
                    chaos_state["rate"]     = 0.0

                elif chaos_mode == "error":
                    # Return 500 on ~rate% of subsequent requests
                    chaos_state["mode"]     = "error"
                    chaos_state["duration"] = 0
                    chaos_state["rate"]     = float(payload.get("rate", 0.5))

                elif chaos_mode == "recover":
                    # Cancel all chaos and return to normal
                    chaos_state["mode"]     = None
                    chaos_state["duration"] = 0
                    chaos_state["rate"]     = 0.0

                else:
                    self.send_json(400, {
                        "error": f"unknown chaos mode: {chaos_mode}"
                    })
                    return

            self.send_json(200, {"ok": True, "chaos": chaos_state})

        else:
            self.send_json(404, {"error": "not found"})

    def log_message(self, format, *args):
        """
        Suppress default Python HTTP server logs.
        Nginx handles all access logging for us.
        """
        pass


# ─────────────────────────────────────────────
# Start the server
# ─────────────────────────────────────────────
if __name__ == "__main__":
    server = HTTPServer(("", APP_PORT), Handler)
    print(f"[swiftdeploy] starting on port {APP_PORT} in {MODE} mode (v{APP_VERSION})")
    server.serve_forever()