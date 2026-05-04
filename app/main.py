# app/main.py
# This is the HTTP API service that runs inside the Docker container.
# It reads configuration from environment variables injected by Docker Compose.

import os           # For reading environment variables
import time         # For uptime tracking and sleep (chaos mode)
import random       # For random error simulation (chaos mode)
import threading    # For thread-safe state management
from datetime import datetime, timezone  # For timestamps
from http.server import HTTPServer, BaseHTTPRequestHandler  # Built-in HTTP server
import json         # For reading/writing JSON bodies

# ─────────────────────────────────────────────
# Read configuration from environment variables
# These are injected by Docker Compose from manifest.yaml
# ─────────────────────────────────────────────
MODE        = os.environ.get("MODE", "stable")         # "stable" or "canary"
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")   # Version string
APP_PORT    = int(os.environ.get("APP_PORT", "3000"))   # Port to listen on

# Record the exact time the server started, so we can calculate uptime
START_TIME = time.time()

# ─────────────────────────────────────────────
# Chaos State
# This dictionary holds the current chaos configuration.
# It is shared across all requests, so we use a lock to keep it thread-safe.
# A "lock" means: only one request can change chaos state at a time.
# ─────────────────────────────────────────────
chaos_lock  = threading.Lock()   # Prevents two requests changing chaos at the same time
chaos_state = {
    "mode": None,       # Current chaos mode: None, "slow", or "error"
    "duration": 0,      # For "slow" mode: how many seconds to sleep
    "rate": 0.0,        # For "error" mode: probability of returning 500
}


def apply_chaos():
    """
    This function is called at the START of every request.
    It checks the current chaos state and applies the effect:
    - "slow": makes the server sleep before responding
    - "error": randomly returns True to signal a 500 error should be sent
    Returns True if we should return a 500 error, False otherwise.
    """
    with chaos_lock:                           # Lock so we safely read chaos_state
        mode = chaos_state["mode"]             # What chaos mode is active?

    if mode == "slow":
        # Sleep for the configured number of seconds before continuing
        time.sleep(chaos_state["duration"])

    elif mode == "error":
        # random.random() returns a float between 0.0 and 1.0
        # If it's less than the configured rate (e.g. 0.5 = 50%), return an error
        if random.random() < chaos_state["rate"]:
            return True   # Signal: send a 500 error

    return False   # No error, continue normally


class Handler(BaseHTTPRequestHandler):
    """
    This class handles every incoming HTTP request.
    Python's HTTPServer calls do_GET() for GET requests, do_POST() for POST requests.
    """

    def send_json(self, status_code, data, extra_headers=None):
        """
        Helper method to send a JSON response.
        status_code: HTTP status code (200, 500, etc.)
        data: Python dict that will be converted to JSON
        extra_headers: optional dict of additional headers to add
        """
        body = json.dumps(data).encode()         # Convert dict to JSON bytes
        self.send_response(status_code)           # Send the HTTP status line
        self.send_header("Content-Type", "application/json")  # Tell client it's JSON
        self.send_header("Content-Length", str(len(body)))    # Tell client body size

        # If in canary mode, add the X-Mode header to EVERY response
        if MODE == "canary":
            self.send_header("X-Mode", "canary")

        # Add any extra headers the caller requested
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)

        self.end_headers()                        # Blank line signals end of headers
        self.wfile.write(body)                    # Send the JSON body

    def do_GET(self):
        """Handles all GET requests. Routes based on the URL path."""

        if self.path == "/":
            # ── GET / ──────────────────────────────────────────────────────
            # Welcome endpoint. Returns mode, version, and current timestamp.
            self.send_json(200, {
                "message": f"Welcome to SwiftDeploy! Running in {MODE} mode.",
                "mode":    MODE,
                "version": APP_VERSION,
                "time":    datetime.now(timezone.utc).isoformat(),  # ISO 8601 timestamp
            })

        elif self.path == "/healthz":
            # ── GET /healthz ───────────────────────────────────────────────
            # Health check endpoint. Docker and swiftdeploy poll this.
            # Returns status "ok" and how many seconds the server has been running.
            uptime = time.time() - START_TIME    # Calculate seconds since start
            self.send_json(200, {
                "status": "ok",
                "uptime": round(uptime, 2),      # Round to 2 decimal places
            })

        else:
            # Any other path returns 404
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        """Handles all POST requests."""

        if self.path == "/chaos":
            # ── POST /chaos ────────────────────────────────────────────────
            # Only available in canary mode.
            # Reads JSON body and updates the chaos state.

            if MODE != "canary":
                # If not in canary mode, chaos is not allowed
                self.send_json(403, {"error": "chaos endpoint only available in canary mode"})
                return

            # Read the request body
            length = int(self.headers.get("Content-Length", 0))  # How many bytes to read
            body   = self.rfile.read(length)                      # Read the body bytes

            try:
                payload = json.loads(body)    # Parse JSON body into Python dict
            except json.JSONDecodeError:
                self.send_json(400, {"error": "invalid JSON"})
                return

            chaos_mode = payload.get("mode")   # What chaos mode did the client request?

            with chaos_lock:   # Lock before modifying shared state
                if chaos_mode == "slow":
                    # Activate slow mode: sleep N seconds before every response
                    chaos_state["mode"]     = "slow"
                    chaos_state["duration"] = int(payload.get("duration", 1))
                    chaos_state["rate"]     = 0.0

                elif chaos_mode == "error":
                    # Activate error mode: return 500 on ~rate% of requests
                    chaos_state["mode"]     = "error"
                    chaos_state["duration"] = 0
                    chaos_state["rate"]     = float(payload.get("rate", 0.5))

                elif chaos_mode == "recover":
                    # Cancel all chaos, return to normal
                    chaos_state["mode"]     = None
                    chaos_state["duration"] = 0
                    chaos_state["rate"]     = 0.0

                else:
                    self.send_json(400, {"error": f"unknown chaos mode: {chaos_mode}"})
                    return

            self.send_json(200, {"ok": True, "chaos": chaos_state})

        else:
            self.send_json(404, {"error": "not found"})

    def do_PATCH(self):
        """Some HTTP clients send PATCH; return 405 Method Not Allowed."""
        self.send_json(405, {"error": "method not allowed"})

    def log_message(self, format, *args):
        """
        Override the default log format.
        By default Python's HTTPServer logs to stderr with its own format.
        We suppress it here because Nginx handles access logging for us.
        """
        pass   # Do nothing — keeps container logs clean


# ─────────────────────────────────────────────
# Start the server
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # "" means "listen on all network interfaces" — required inside Docker
    server = HTTPServer(("", APP_PORT), Handler)
    print(f"[swiftdeploy] app starting on port {APP_PORT} in {MODE} mode (v{APP_VERSION})")
    server.serve_forever()   # Block forever, handling requests