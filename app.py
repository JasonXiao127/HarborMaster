from flask import Flask, jsonify, render_template
import docker
import psutil
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Cache for CPU delta calculations
CPU_CACHE = {}

def get_docker_client():
    try:
        # docker.from_env() automatically reads the DOCKER_HOST env variable 
        # (tcp://socket-proxy:2375) we specified in docker-compose.yml
        client = docker.from_env()
        client.ping()
        return client
    except Exception as e:
        logger.error(f"Failed to connect to the Docker TCP Proxy: {e}")
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

def get_container_stats(container):
    if container.status != "running":
        return {"cpu_percent": 0.0, "memory_usage_mb": 0.0, "memory_limit_mb": 0.0}
    try:
        stats = container.stats(stream=False)
        mem_use = stats.get("memory_stats", {}).get("usage", 0)
        mem_limit = stats.get("memory_stats", {}).get("limit", 1)
        
        return {
            "cpu_percent": calculate_cpu_percent(container.id, stats),
            "memory_usage_mb": round(mem_use / (1024 * 1024), 2),
            "memory_limit_mb": round(mem_limit / (1024 * 1024), 2)
        }
    except Exception:
        return {"cpu_percent": 0.0, "memory_usage_mb": 0.0, "memory_limit_mb": 0.0}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/metrics')
def get_metrics():
    client = get_docker_client()
    if not client:
        return jsonify({
            "error": "Cannot connect to the Docker socket proxy. Check proxy container logs."
        }), 500

    containers_data = []
    total_containers_cpu = 0.0
    total_containers_mem_bytes = 0

    try:
        for container in client.containers.list(all=True):
            # Ignore the socket-proxy itself to keep the UI clean
            if container.name == "docker_socket_proxy":
                continue
                
            stats = get_container_stats(container)
            total_containers_cpu += stats["cpu_percent"]
            total_containers_mem_bytes += stats["memory_usage_mb"] * 1024 * 1024

            containers_data.append({
                "id": container.short_id,
                "name": container.name,
                "status": container.status,
                "cpu": stats["cpu_percent"],
                "memory": stats["memory_usage_mb"],
                "memory_limit": stats["memory_limit_mb"]
            })
        
        sys_mem = psutil.virtual_memory().total
        container_mem_pct = round(((total_containers_mem_bytes) / sys_mem) * 100, 2) if sys_mem > 0 else 0
        
        return jsonify({
            "containers": containers_data,
            "system": {
                "containers_total_cpu_percent": round(total_containers_cpu, 2),
                "containers_total_mem_percent": container_mem_pct,
                "host_total_memory_mb": round(sys_mem / (1024 * 1024), 2)
            }
        })
    except Exception as e:
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
