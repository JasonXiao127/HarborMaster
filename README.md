# ⚓ HarborMaster

HarborMaster is a lightweight, containerized web application designed to monitor your Docker containers and system resource usage in real-time. Built with a focus on simplicity, ease of deployment, and security.

<img width="1062" height="485" alt="image" src="https://github.com/user-attachments/assets/2d79126e-8da7-40ce-8694-1773afdc6890" />

---
##  Features

* **Real-Time Container Metrics:** Instantly track CPU and memory usage of individual containers.
* **System Footprint Overview:** View cumulative container resource usage represented as a percentage of overall host system capabilities.
* **Interactivity:** Start, stop, and restart containers directly from the intuitive web interface.
* **Configurable Polling:** Dynamically adjust the update frequency (1s, 3s, 5s) via the UI.
* **Security-Hardened:** Utilizes a Docker Socket Proxy to restrict API access, with non-root read-only containers, no `pid:host`, and optional API token auth.

---

##  Architecture

HarborMaster enforces a **two-container architecture** to guarantee security isolation and performance:

1. **`socket-proxy`**: A TCP proxy (`tecnativa/docker-socket-proxy`) between the host socket and the app. It allows container `list`/`stats` plus `start`/`stop`/`restart` (`CONTAINERS=1,POST=1,ALLOW_START/STOP/RESTARTS=1`) and blocks other API sections. Note: the proxy's generic `/containers` rule still permits other container POSTs, so keep it on the internal network only.
2. **`monitor`**: The Python Flask web server. It connects to the proxy via TCP (rather than mounting the raw Unix socket) and runs as a non-root read-only user.

---

## Deployment Instructions

This project was meant to deploy strictly with docker compose. Environmental Variables are an option to modify the default ports

### Quick Start (TL;DR)
```bash
git clone https://github.com/JasonXiao127/HarborMaster.git
cd HarborMaster
cp .env.example .env  # optional; set MONITOR_AUTH_TOKEN to require API auth
docker compose up -d --build
```

Local dev (without Docker daemon, `/api/metrics` returns 500 until Docker is up):
```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Env vars: `APP_HOST_PORT` (default `127.0.0.1:5000`), `MONITOR_AUTH_TOKEN`
(empty = open), `DOCKER_TIMEOUT`, `STATS_TIMEOUT`.

Security notes: `socket-proxy` uses `CONTAINERS=1,POST=1` plus
`ALLOW_START/STOP/RESTARTS=1`, but the proxy's generic `/containers`
rule still permits other container POSTs — treat it as defense-in-depth,
keep the port on localhost / behind auth, and never expose `2375`.
`monitor` runs as non-root (`65532`), read-only, no `pid:host`. `socket-proxy`
needs root to read `/var/run/docker.sock`, so it stays root but with
`cap_drop: ALL`, `no-new-privileges`, read-only fs.

`APP_HOST_PORT` must stay in `IP:PORT` form (e.g. `127.0.0.1:5000`); a bare
`5000` publishes on all interfaces.



