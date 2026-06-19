from flask import Flask, jsonify, render_template
import docker
import psutil
from concurrent.futures import ThreadPoolExecutor
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CPU_CACHE = {}

def get_docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client
    except Exception as e:
        logger.error(f"Failed to connect to Docker socket: {e}")
        return None

def calculate_cpu_percent(container_id, stats):
    try:
        cpu_stats = stats.get("cpu_stats", {})
        cpu_usage = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        system_usage = cpu_stats.get("system_cpu_usage", 0)

        prev = CPU_CACHE.get(container_id)
        CPU_CACHE[container_id] = (cpu_usage, system_usage)

        if prev is None:
            precpu_stats = stats.get("precpu_stats", {})
            prev_cpu = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
            prev_system = precpu_stats.get("system_cpu_usage", 0)
        else:
            prev_cpu, prev_system = prev

        cpu_delta = cpu_usage - prev_cpu
        system_delta = system_usage - prev_system

        if system_delta > 0.0 and cpu_delta > 0.0:
            num_cpus = cpu_stats.get("online_cpus", len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [])))
            num_cpus = num_cpus or 1
            return round((cpu_delta / system_delta) * num_cpus * 100.0, 2)
    except Exception:
        pass
    return 0.0

def get_single_container_data(container):
    """Processes a single container. Safe to run in parallel threads."""
    data = {
        "id": container.short_id,
        "name": container.name,
        "status": container.status,
        "cpu": 0.0,
        "memory": 0.0,
        "memory_limit": 0.0
    }
    
    if container.status != "running":
        return data

    try:
        stats = container.stats(stream=False)
        
        # MEMORY CALCULATION CORRECTION (Matching 'docker stats' and 'btop')
        mem_stats = stats.get("memory_stats", {})
        usage = mem_stats.get("usage", 0)
        
        # Subtract filesystem cache (inactive_file on cgroups v2, cache on cgroups v1)
        details = mem_stats.get("stats", {})
        cache = details.get("inactive_file", details.get("cache", 0))
        
        # Real Active RSS Memory
        real_mem = max(0, usage - cache)
        mem_limit = mem_stats.get("limit", 1)
        
        data["cpu"] = calculate_cpu_percent(container.id, stats)
        data["memory"] = round(real_mem / (1024 * 1024), 2)
        data["memory_limit"] = round(mem_limit / (1024 * 1024), 2)
    except Exception as e:
        logger.debug(f"Error fetching stats for {container.name}: {e}")
        
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
        # Exclude the secure proxy from your lists
        filtered_containers = [c for c in containers if c.name != "docker_socket_proxy"]

        # Solve the latency issue: fetch stats in parallel using threads
        with ThreadPoolExecutor(max_workers=10) as executor:
            containers_data = list(executor.map(get_single_container_data, filtered_containers))

        total_containers_cpu = sum(c["cpu"] for c in containers_data)
        total_containers_mem_bytes = sum(c["memory"] for c in containers_data) * 1024 * 1024

        sys_mem = psutil.virtual_memory().total
        container_mem_pct = round((total_containers_mem_bytes / sys_mem) * 100, 2) if sys_mem > 0 else 0
        
        return jsonify({
            "containers": containers_data,
            "system": {
                "containers_total_cpu_percent": round(total_containers_cpu, 2),
                "containers_total_mem_percent": container_mem_pct,
                "host_total_memory_mb": round(sys_mem / (1024 * 1024), 2)
            }
        })
    except Exception as e:
        logger.error(f"Error in API: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/containers/<container_id>/<action>', methods=['POST'])
def container_action(container_id, action):
    client = get_docker_client()
    if not client: 
        return jsonify({"error": "No Docker connection"}), 500
    try:
        container = client.containers.get(container_id)
        if action == "start": container.start()
        elif action == "stop": container.stop()
        elif action == "restart": container.restart()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
