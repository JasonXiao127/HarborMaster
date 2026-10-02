# HarborMaster

Web UI to watch Docker CPU and memory and to start, stop, and restart containers.

<img width="1062" height="485" alt="HarborMaster dashboard screenshot" src="https://github.com/user-attachments/assets/2d79126e-8da7-40ce-8694-1773afdc6890" />

## Features

* Per container CPU percent and memory use
* Total CPU and host memory share
* Start, stop, restart from the UI
* Poll rate switch: 1s, 3s, 5s

## Requirements

* Docker and Docker Compose
* Linux host with `/var/run/docker.sock` for full deploy. Local UI runs without it but metrics return 500.

## Quick start

```bash
git clone https://github.com/JasonXiao127/HarborMaster.git
cd HarborMaster
cp .env.example .env
docker compose up -d --build
```

Open http://127.0.0.1:5000

## Config

| Var | Default | Notes |
| --- | --- | --- |
| `APP_HOST_PORT` | `127.0.0.1:5000` | Keep `IP:PORT` form. A bare `5000` listens on all interfaces. |
| `APP_PORT` | `5000` | Container port. Gunicorn and healthchecks follow it. |
| `MONITOR_AUTH_TOKEN` | empty | When set, `/api/*` needs `X-Auth-Token` header or `Authorization: Bearer`. Empty means open. The UI forwards `?token=` from the page URL as a header. |
| `DOCKER_TIMEOUT` | `5` | Seconds for Docker client calls. |
| `STATS_TIMEOUT` | `5` | Seconds for per container stats. |
| `ACTION_TIMEOUT` | `10` | Seconds for start, stop, restart calls. |

## How it works

Two containers on an internal network:

1. `socket-proxy` (`tecnativa/docker-socket-proxy:0.4.1`). Sits between the Docker socket and the app. Allows `list` and `stats` plus `start`, `stop`, `restart`. Other sections stay off.
2. `monitor`. Flask app served by gunicorn. Talks to the proxy over TCP. Runs as non-root user `65532`, read only.

Note: the proxy generic `/containers` rule still allows other container POSTs, so keep port `2375` internal only.

## Security

* Binds to `127.0.0.1` by default. Set `APP_HOST_PORT` to expose it, and set a token first.
* `monitor` is non-root, read only, no `pid:host`, with `cap_drop: ALL`.
* `socket-proxy` needs root to read the socket. It uses `cap_drop: ALL`, `no-new-privileges`, and a read only fs to limit it.
* Never publish the proxy port.

## Local dev

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Problems

* `/api/metrics` 500 `Cannot connect to the Docker daemon proxy`: Docker is off or the proxy is not up. Start Docker Desktop and run `docker compose up -d`.
* Port in use: change `APP_HOST_PORT` in `.env`, for example `127.0.0.1:5001`.
* 401 on `/api/*`: you set a token. Send it as `X-Auth-Token` or `Authorization: Bearer`. In a browser you can also open `http://127.0.0.1:5000/?token=YOURTOKEN` once and the UI remembers it.
* 409 `Container already in that state`: the container is already started or stopped. Refresh the list.
* Windows without Docker socket: use the local dev steps above for UI work only.
