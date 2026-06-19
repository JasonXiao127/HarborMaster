# ⚓ HarborMaster

HarborMaster is a lightweight, containerized web application designed to monitor your Docker containers and system resource usage in real-time. Built with a focus on simplicity, ease of deployment, and security.

---
##  Features

* **Real-Time Container Metrics:** Instantly track CPU and memory usage of individual containers.
* **System Footprint Overview:** View cumulative container resource usage represented as a percentage of overall host system capabilities.
* **Interactivity:** Start, stop, and restart containers directly from the intuitive web interface.
* **Configurable Polling:** Dynamically adjust the update frequency (1s, 3s, 5s) via the UI.
* **Security-Hardened:** Utilizes a read-only Docker Socket Proxy to completely isolate and protect your host system from unauthorized API access.

---

##  Architecture

HarborMaster enforces a **two-container architecture** to guarantee security isolation and performance:

1. **`socket-proxy`**: Houses a secure TCP proxy (`tecnativa/docker-socket-proxy`) that sits directly between the host socket and the application. It restricts the host's `/var/run/docker.sock` to only allow essential, safe read/write operations (`container stats`, `list`, `start`, `stop`, `restart`), entirely mitigating container escape exploits.
2. **`monitor`**: The Python Flask web server. It connects to the proxy securely via TCP (rather than mounting the raw Unix socket directly) and runs under a standard, non-privileged user account.

---

## 📂 Directory Structure

Ensure your project workspace contains the following files before deploying:

```text
docker-monitor/
├── app.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── README.md
└── templates/
    └── index.html
