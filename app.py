from flask import Flask, jsonify, render_template, request
import docker
from docker.errors import NotFound, APIError
import psutil
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- Config (env-overridable, no code changes needed) ---
HOST = os.getenv("HOST", "0.0.0.0")


def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f"Invalid numeric env value {value!r}, using {default}")
        return default


PORT = _safe_int(os.getenv("APP_PORT", os.getenv("PORT", "5000")), 5000)
AUTH_TOKEN = os.getenv("MONITOR_AUTH_TOKEN", "")
DOCKER_TIMEOUT = _safe_int(os.getenv("DOCKER_TIMEOUT", "5"), 5)
STATS_TIMEOUT = _safe_int(os.getenv("STATS_TIMEOUT", "5"), 5)

CPU_CACHE = {}
CACHE_LOCK = threading.Lock()
MAX_CACHE_SIZE = 1000

ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
ALLOWED_ACTIONS = {"start", "stop", "restart"}
PROTECTED_NAMES = {"docker_socket_proxy", "docker_resource_monitor"}

_auth_warned = False


def is_authorized(req):
    """Optional bearer/token auth. Empty MONITOR_AUTH_TOKEN = open (legacy)."""
    global _auth_warned
    if not AUTH_TOKEN:
        if not _auth_warned:
            logger.warning("MONITOR_AUTH_TOKEN not set — API is unauthenticated.")
            _auth_warned = True
        return True
    supplied = (
        req.headers.get("X-Auth-Token")
        or req.args.get("token")
        or None
    )
    authz = req.headers.get("Authorization", "")
    if not supplied and authz.startswith("Bearer "):
        supplied = authz[len("Bearer "):].strip() or None
    return supplied == AUTH_TOKEN


@app.before_request
def enforce_auth():
    if (request.path == "/api" or request.path.startswith("/api/")) and not is_authorized(request):
        return jsonify({"error": "Unauthorized"}), 401


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; object-src 'none'"
    )
    return resp


def get_docker_client():
    try:
        client = docker.from_env(timeout=DOCKER_TIMEOUT)
        client.ping()
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Docker socket: {e}")
        return None


def _prune_cache(valid_ids):
    with CACHE_LOCK:
        stale = [k for k in CPU_CACHE if k not in valid_ids]
        for k in stale:
            CPU_CACHE.pop(k, None)
        # Hard cap against churn (e.g. short-lived CI containers)
        while len(CPU_CACHE) > MAX_CACHE_SIZE:
            CPU_CACHE.pop(next(iter(CPU_CACHE)), None)


def calculate_cpu_percent(container_id, stats):
    try:
        cpu_stats = stats.get("cpu_stats", {}) if isinstance(stats, dict) else {}
        cpu_usage = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) or 0
        system_usage = cpu_stats.get("system_cpu_usage", 0) or 0

        with CACHE_LOCK:
            prev = CPU_CACHE.get(container_id)
            CPU_CACHE[container_id] = (cpu_usage, system_usage)

        if prev is None:
            precpu_stats = stats.get("precpu_stats", {}) if isinstance(stats, dict) else {}
            prev_cpu = precpu_stats.get("cpu_usage", {}).get("total_usage", 0) or 0
            prev_system = precpu_stats.get("system_cpu_usage", 0) or 0
        else:
            prev_cpu, prev_system = prev

        cpu_delta = cpu_usage - prev_cpu
        system_delta = system_usage - prev_system

        if system_delta > 0 and cpu_delta > 0:
            percpu = cpu_stats.get("cpu_usage", {}).get("percpu_usage") or []
            num_cpus = cpu_stats.get("online_cpus") or len(percpu) or 1
            try:
                num_cpus = int(num_cpus)
            except (TypeError, ValueError):
                num_cpus = 1
            num_cpus = max(1, num_cpus)
            value = (cpu_delta / system_delta) * num_cpus * 100.0
            # Clamp: counter resets / first-poll spikes shouldn't exceed all cores
            return round(max(0.0, min(value, 100.0 * num_cpus)), 2)
    except Exception as e:
        logger.debug(f"CPU calc failed for {container_id}: {e}")
    return 0.0


def get_single_container_data(container):
    """Fetch one container's stats. Never raises — returns zeros on failure."""
    try:
        short_id = getattr(container, "short_id", "?")
        name = getattr(container, "name", "?")
        status = getattr(container, "status", "?")
        full_id = getattr(container, "id", short_id)
    except Exception as e:
        logger.warning(f"Stale container handle: {e}")
        return {"id": "?", "name": "?", "status": "unknown",
                "cpu": 0.0, "memory": 0.0, "memory_limit": 0.0}

    data = {"id": short_id, "name": name, "status": status,
            "cpu": 0.0, "memory": 0.0, "memory_limit": 0.0,
            "_mem_bytes": 0}

    if status != "running":
        return data

    try:
        stats = container.stats(stream=False)
        if not isinstance(stats, dict):
            return data

        mem_stats = stats.get("memory_stats", {}) or {}
        usage = mem_stats.get("usage") or 0
        details = mem_stats.get("stats", {}) or {}
        cache = details.get("inactive_file", details.get("cache", 0)) or 0
        try:
            usage, cache = int(usage), int(cache)
        except (TypeError, ValueError):
            usage, cache = 0, 0

        real_mem = max(0, usage - cache)
        mem_limit = mem_stats.get("limit") or 0
        try:
            mem_limit = int(mem_limit)
        except (TypeError, ValueError):
            mem_limit = 0

        data["cpu"] = calculate_cpu_percent(full_id, stats)
        data["_mem_bytes"] = real_mem
        data["memory"] = round(real_mem / (1024 * 1024), 2)
        data["memory_limit"] = round(mem_limit / (1024 * 1024), 2) if mem_limit else 0.0
    except (NotFound, APIError) as e:
        logger.warning(f"Stats unavailable for {name}: {e}")
    except Exception as e:
        logger.warning(f"Error fetching stats for {name}: {e}", exc_info=True)

    return data


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/metrics')
def get_metrics():
    client = get_docker_client()
    if not client:
        return jsonify({"error": "Cannot connect to the Docker daemon proxy."}), 500

    try:
        containers = client.containers.list(all=True)
        filtered = [c for c in containers
                    if getattr(c, "name", "") not in PROTECTED_NAMES]

        with ThreadPoolExecutor(max_workers=10) as executor:
            containers_data = list(executor.map(
                get_single_container_data, filtered, timeout=STATS_TIMEOUT * 2))

        _prune_cache({getattr(c, "id", getattr(c, "short_id", "")) for c in filtered})

        total_cpu = round(sum(c.get("cpu", 0.0) for c in containers_data), 2)
        total_mem_bytes = sum(c.pop("_mem_bytes", 0) for c in containers_data)

        sys_mem = psutil.virtual_memory().total or 0
        mem_pct = round((total_mem_bytes / sys_mem) * 100, 2) if sys_mem > 0 else 0

        return jsonify({
            "containers": containers_data,
            "system": {
                "containers_total_cpu_percent": total_cpu,
                "containers_total_mem_percent": mem_pct,
                "host_total_memory_mb": round(sys_mem / (1024 * 1024), 2)
            }
        })
    except Exception as e:
        logger.error("Error in /api/metrics", exc_info=True)
        return jsonify({"error": "Internal server error."}), 500


@app.route('/api/containers/<container_id>/<action>', methods=['POST'])
def container_action(container_id, action):
    if not ID_RE.match(container_id or "") or len(container_id) > 128:
        return jsonify({"error": "Invalid container id."}), 400
    if action not in ALLOWED_ACTIONS:
        return jsonify({"error": f"Invalid action. Allowed: {sorted(ALLOWED_ACTIONS)}"}), 400

    client = get_docker_client()
    if not client:
        return jsonify({"error": "No Docker connection"}), 500
    try:
        container = client.containers.get(container_id)
        if getattr(container, "name", "") in PROTECTED_NAMES:
            logger.warning(f"Blocked {action} on protected container {container_id}")
            return jsonify({"error": "Operation not allowed on infrastructure containers."}), 403
        if action == "start":
            container.start()
        elif action == "stop":
            container.stop(timeout=10)
        elif action == "restart":
            container.restart(timeout=10)
        logger.info(f"Container {action}: {container_id}")
        return jsonify({"status": "success"})
    except NotFound:
        return jsonify({"error": "Container not found."}), 404
    except Exception:
        logger.error(f"Container {action} failed for {container_id}", exc_info=True)
        return jsonify({"error": "Action failed."}), 500


if __name__ == '__main__':
    # Explicitly non-debug; use gunicorn in production (see Dockerfile).
    app.run(host=HOST, port=PORT, debug=False)
