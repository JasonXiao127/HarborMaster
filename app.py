from flask import Flask, jsonify, render_template, request
import docker
import psutil
import os

app = Flask(__name__)

try:
    client = docker.from_env()
except Exception as e:
    print(f"Error connecting to Docker daemon: {e}")
    client = None

def calculate_cpu_percent(stats):
    try:
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})
        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
        if system_delta > 0.0 and cpu_delta > 0.0:
            num_cpus = cpu_stats.get("online_cpus", len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [])))
            return round((cpu_delta / system_delta) * (num_cpus or 1) * 100.0, 2)
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
            "cpu_percent": calculate_cpu_percent(stats),
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
    if not client: return jsonify({"error": "No Docker connection"}), 500
    containers_data = []
    total_containers_cpu = 0.0
    total_containers_mem_bytes = 0
    try:
        for container in client.containers.list(all=True):
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
        container_mem_pct = round(((total_containers_mem_bytes) / sys_mem) * 100, 2)
        
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

# FIXED: Removed the 'list_methods' argument that caused the crash
@app.route('/api/containers/<container_id>/<action>', methods=['POST'])
def container_action(container_id, action):
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
