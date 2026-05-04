# SwiftDeploy: Declarative DevOps Lifecycle Tool

**SwiftDeploy** is a lightweight, declarative CLI tool designed to simplify containerized infrastructure management. It derives the entire lifecycle of a production-ready deployment—including an HTTP API service, Nginx reverse proxy, and Docker Compose topology—from a single `manifest.yaml` file [INDEX]. 

No configurations are handwritten; the manifest acts as the **single source of truth** [INDEX].

---

## Project Structure

```text
├── app/
│   └── main.py
├── templates/
│   ├── docker-compose.yml.tmpl
│   └── nginx.conf.tmpl
├── Dockerfile
├── manifest.yaml
├── README.md
└── swiftdeploy
```

---

## Prerequisites

Ensure the following tools are installed on your host machine:
* **Docker** & **Docker Compose V2**
* **Python 3** (with `PyYAML` installed: `pip install pyyaml`) [INDEX]
* **curl** (for deployment polling and health confirmation) [INDEX]
* **netcat** or **lsof** (for port availability checks) [INDEX]

---

## 1. Declarative Manifest Setup

### `manifest.yaml`
This is the only configuration file you should manually edit.

```yaml
services:
  image: swift-deploy-1-node:latest
  port: 3000
  mode: stable
  version: "1.0.0"
  restart: unless-stopped

nginx:
  image: nginx:latest
  port: 8080
  proxy_timeout: 30

network:
  name: swiftdeploy-net
  driver_type: bridge

contact: "admin@swiftdeploy.local"
```

---

## 2. Walkthrough of the CLI Subcommands

### Make the CLI Executable
Before running any subcommand, grant executable permissions to the `swiftdeploy` script:
```bash
chmod +x swiftdeploy
```

---

### `init`
Parses `manifest.yaml` and generates both `nginx.conf` and `docker-compose.yml` dynamically from the `.tmpl` files in the `templates/` directory [INDEX].

```bash
./swiftdeploy init
```

* **Inputs:** Reads configurations from `manifest.yaml` and processes placeholders in template files.
* **Outputs:** Generates `nginx.conf` and `docker-compose.yml` in the project root [INDEX].

---

### `validate`
Runs 5 pre-flight checks and exits with a non-zero status code on any configuration or infrastructure failure [INDEX].

```bash
./swiftdeploy validate
```

* **Pass Criteria:**
  1. `manifest.yaml` exists and is valid YAML [INDEX].
  2. All required fields (`services`, `nginx`, `network`) are present and non-empty [INDEX].
  3. The specified Docker image exists in the local cache [INDEX].
  4. The specified Nginx port (`8080`) is not already bound on the host [INDEX].
  5. The generated `nginx.conf` passes the Nginx syntax test via containerized validation [INDEX].

---

### `deploy`
Executes initialization, performs all validation steps, brings up the infrastructure stack, and blocks until the application's health check passes or a 60-second timeout expires [INDEX].

```bash
./swiftdeploy deploy
```

* **Process:** Automatically triggers `init` and `validate`, starts up the Nginx proxy and Python API service via Docker Compose, and verifies that `http://localhost:8080/healthz` returns HTTP status `200` [INDEX].

---

### `promote`
Updates the deployment mode in-place within `manifest.yaml`, recreates the backend application container using a rolling service restart, and verifies the new mode is active [INDEX].

```bash
# Switch deployment to canary mode
./swiftdeploy promote canary

# Switch deployment back to stable mode
./swiftdeploy promote stable
```

* **Process:** 
  1. Updates the `mode` field inside `manifest.yaml` [INDEX].
  2. Regenerates `docker-compose.yml` with the updated environment configurations [INDEX].
  3. Recreates the `app` container only, ensuring no Nginx downtime [INDEX].
  4. Polls `http://localhost:8080/healthz` to verify the mode switch has taken effect [INDEX].

---

### `teardown`
Stops running services and removes the containers, networks, and persistent storage volumes [INDEX].

```bash
# Stop and remove the deployment stack
./swiftdeploy teardown

# Stop the stack and purge all generated configuration files
./swiftdeploy teardown --clean
```

---

## 3. The API Service Endpoints

The API is fully isolated behind the Nginx reverse proxy. Once the stack is deployed, it can be reached on `http://localhost:8080` [INDEX].

### GET `/`
Returns welcoming API metadata, operating mode, and the current server timestamp [INDEX].

```bash
curl http://localhost:8080/
```
* **Canary Bonus:** If the app is promoted to `canary` mode, the response also includes the `X-Mode: canary` HTTP response header [INDEX].

---

### GET `/healthz`
Evaluates the liveness of the service. Returns the uptime and current operating state [INDEX].

```bash
curl http://localhost:8080/healthz
```

---

### POST `/chaos`
*Requires canary mode.* Accepts a JSON payload to simulate degraded infrastructure behaviors [INDEX].

#### 1. Slow Mode (Slowness Injection)
Causes the API to sleep for $N$ seconds before returning any response [INDEX].
```bash
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode": "slow", "duration": 5}'
```

#### 2. Error Mode (Dynamic Failures)
Forces the application to simulate an HTTP 500 server error at the specified probability rate (e.g., $0.5 = 50\%$) [INDEX].
```bash
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode": "error", "rate": 0.5}'
```

#### 3. Recover Mode
Restores the API service to its normal operating state by canceling any active chaos simulations [INDEX].
```bash
curl -X POST http://localhost:8080/chaos \
  -H "Content-Type: application/json" \
  -d '{"mode": "recover"}'
```
