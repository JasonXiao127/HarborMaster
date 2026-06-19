from flask import Flask, jsonify, render_template, request
import docker
import psutil

app = Flask(__name__)

try:
    # Connects to the Docker daemon via the default local socket
    client = docker.from_env()
except Exception as e:
    print(f"Error connecting to Docker daemon: {e}")
    client = None

def calculate_cpu_percent(stats):
    """Calculates CPU usage percentage from raw docker stats."""
    try:
        cpu_stats = stats.get("cpu_stats", {})
        precpu_stats = stats.get("precpu_stats", {})

        cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)

        if system_delta > 0.0 and cpu_delta > 0.0:
            # Get number of active CPUs
            num_cpus = cpu_stats.get("online_cpus", len(cpu_stats.get("cpu_usage", {}).get("percpu_usage", [])))
            if num_cpus == 0:
                num_cpus = 1
            return round((cpu_delta / system_delta) * num_cpus * 100.0, 2)
    except KeyError:
        pass
    return 0.0

def get_container_stats(container):
    """Fetches key stats for a single container."""
    if container.status != "running":
        return {"cpu_percent": 0.0, "memory_usage_mb": 0.0, "memory_limit_mb": 0.0}

    try:
        # stream=False returns a single snapshot of stats
        stats = container.stats(stream=False)
        
        # Memory usage
        mem_use = stats.get("memory_stats", {}).get("usage", 0)
        mem_limit = stats.get("memory_stats", {}).get("limit", 1) # Prevent division by zero
        
        # Convert bytes to Megabytes
        mem_use_mb = round(mem_use / (1024 * 1024), 2)
        mem_limit_mb = round(mem_limit / (1024 * 1024), 2)
        
        cpu_percent = calculate_cpu_percent(stats)
        
        return {
            "cpu_percent": cpu_percent,
            "memory_usage_mb": mem_use_mb,
            "memory_limit_mb": mem_limit_mb
        }
    except Exception:
        return {"cpu_percent": 0.0, "memory_usage_mb": 0.0, "memory_limit_mb": 0.0}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/metrics')
def get_metrics():
    if not client:
        return jsonify({"error": "Docker connection unavailable"}), 500

    containers_data = []
    total_containers_cpu = 0.0
    total_containers_mem_bytes = 0

    try:
        # Retrieve all containers (including stopped ones)
        containers = client.containers.list(all=True)
        
        for container in containers:
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
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # System-wide calculations
    try:
        host_total_memory = psutil.virtual_memory().total
        # psutil.cpu_percent with interval=None is non-blocking but might be less precise than interval=0.1.
        # However, to avoid blocking the API request, we use None.
        host_cpu_percent = psutil.cpu_percent(interval=None) 
    except Exception:
        host_total_memory = 1
        host_cpu_percent = 0.0

    # Calculate overall container footprint as a % of system total
    system_mem_mb = host_total_memory / (1024 * 1024)
    total_containers_mem_mb = total_containers_mem_bytes / (1024 * 1024)
    
    container_mem_pct_of_host = round((total_containers_mem_mb / system_mem_mb) * 100, 2) if system_mem_mb > 0 else 0

    return jsonify({
        "containers": containers_data,
        "system": {
            "host_cpu_percent": host_cpu_percent,
            "host_total_memory_mb": round(system_mem_mb, 2),
            "containers_total_cpu_percent": round(total_containers_cpu, 2),
            "containers_total_mem_percent": container_mem_pct_of_host,
            "containers_total_mem_mb": round(total_containers_mem_mb, 2)
        }
    })

@app.route('/api/containers/<container_id>/<action>', list_methods=None, methods=['POST'])
def container_action(container_id, action):
    if not client:
        return jsonify({"error": "Docker connection unavailable"}), 500
        
    try:
        container = client.containers.get(container_id)
        if action == "start":
            container.start()
        elif action == "stop":
            container.stop()
        elif action == "restart":
            container.restart()
        else:
            return jsonify({"error": "Invalid action"}), 400
        return jsonify({"status": "success", "message": f"Container {action}ed successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Running on 0.0.0.0 to make it accessible inside docker
    app.run(host='0.0.0.0', port=5000, debug=True)