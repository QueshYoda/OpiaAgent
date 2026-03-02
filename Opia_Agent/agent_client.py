import grpc
import psutil
import time
import docker
import subprocess
import urllib.request

import agent_pb2
import agent_pb2_grpc

SERVER_ADDRESS = '192.168.0.100:5054' 
SERVER_ID = 'fedora-node-01'

# cloudflared'i de izleme listemize aldık
TARGET_SERVICES = ['jenkins', 'postgresql', 'cloudflared'] 

LAST_SERVICES_STATE = []
LAST_ERRORS_STATE = []
LAST_CONTAINERS_STATE = []

try:
    docker_client = docker.from_env()
except Exception:
    docker_client = None

def get_service_metrics(service_name):
    """Bir servisin durumunu, CPU ve RAM (MB) kullanımını döndürür."""
    status = "unknown"
    cpu = 0.0
    mem_mb = 0.0
    try:
        # Önce servisin durumunu al
        res_status = subprocess.run(['systemctl', 'is-active', service_name], capture_output=True, text=True)
        status = res_status.stdout.strip()
        
        if status == "active":
            # Servisin Ana İşlem ID'sini (PID) bul
            res_pid = subprocess.run(['systemctl', 'show', '-p', 'MainPID', '--value', service_name], capture_output=True, text=True)
            pid_str = res_pid.stdout.strip()
            
            if pid_str and pid_str != '0':
                process = psutil.Process(int(pid_str))
                cpu = process.cpu_percent(interval=0.1)
                mem_mb = process.memory_info().rss / (1024 * 1024) # Byte to MB
    except Exception:
        pass
    
    return status, round(cpu, 2), round(mem_mb, 2)

def get_cloudflare_requests():
    """Cloudflare Tüneli üzerinden geçen toplam HTTP istek sayısını Prometheus metriklerinden çeker."""
    try:
        req = urllib.request.urlopen('http://localhost:2000/metrics', timeout=2)
        lines = req.read().decode('utf-8').split('\n')
        for line in lines:
            if line.startswith('cloudflared_tunnel_total_requests'):
                # Örnek satır: cloudflared_tunnel_total_requests 1542
                return float(line.split(' ')[1])
    except Exception:
        return 0.0
    return 0.0

def get_recent_errors():
    current_errors = []
    try:
        result = subprocess.run(['journalctl', '-p', '3', '-n', '5', '--no-pager'], capture_output=True, text=True)
        lines = result.stdout.strip().split('\n')
        current_errors = [line[:200] for line in lines if line]
    except Exception as e:
        current_errors.append(f"Log hatasi: {e}")
    return current_errors

def push_metrics(stub):
    global LAST_SERVICES_STATE, LAST_ERRORS_STATE, LAST_CONTAINERS_STATE
    
    while True:
        try:
            sys_cpu = psutil.cpu_percent(interval=1)
            sys_mem = psutil.virtual_memory().percent
            cf_requests = get_cloudflare_requests()

            # 1. Container Verileri (CPU ve RAM dahil)
            current_containers = []
            if docker_client:
                for c in docker_client.containers.list(all=True):
                    health = c.attrs['State']['Health']['Status'] if 'State' in c.attrs and 'Health' in c.attrs['State'] else "none"
                    
                    # Kaynak hesaplaması
                    cpu_perc = 0.0
                    mem_mb = 0.0
                    if c.status == 'running':
                        stats = c.stats(stream=False) # Anlık stat çek
                        
                        # Docker CPU formülü (cgroups v1 ve v2 uyumlu)
                        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage'].get('total_usage', 0)
                        
                        system_cpu_usage = stats['cpu_stats'].get('system_cpu_usage')
                        precpu_system_cpu_usage = stats['precpu_stats'].get('system_cpu_usage')
                        
                        if system_cpu_usage is not None and precpu_system_cpu_usage is not None:
                            system_cpu_delta = system_cpu_usage - precpu_system_cpu_usage
                            if system_cpu_delta > 0.0 and cpu_delta > 0.0:
                                # cgroups v2'de 'online_cpus' vardır, yoksa 'percpu_usage' uzunluğuna bakar, o da yoksa 1 sayar.
                                online_cpus = stats['cpu_stats'].get('online_cpus')
                                if online_cpus is None:
                                    percpu_usage = stats['cpu_stats']['cpu_usage'].get('percpu_usage')
                                    online_cpus = len(percpu_usage) if percpu_usage else 1
                                
                                cpu_perc = (cpu_delta / system_cpu_delta) * online_cpus * 100.0
                        
                        mem_mb = stats['memory_stats']['usage'] / (1024 * 1024)

                    current_containers.append({
                        'id': c.short_id, 'name': c.name, 'state': c.status, 'health': health,
                        'cpu': round(cpu_perc, 2), 'mem': round(mem_mb, 2)
                    })

            # 2. Servis Verileri (CPU ve RAM dahil)
            current_services = []
            for srv in TARGET_SERVICES:
                st, c, m = get_service_metrics(srv)
                current_services.append({'name': srv, 'status': st, 'cpu': c, 'mem': m})

            current_errors = get_recent_errors()

            # --- DELTA KONTROLÜ ---
            containers_to_send = []
            if current_containers != LAST_CONTAINERS_STATE:
                containers_to_send = [agent_pb2.ContainerInfo(container_id=c['id'], name=c['name'], state=c['state'], health=c['health'], cpu_percent=c['cpu'], memory_mb=c['mem']) for c in current_containers]
                LAST_CONTAINERS_STATE = current_containers

            services_to_send = []
            if current_services != LAST_SERVICES_STATE:
                services_to_send = [agent_pb2.ServiceInfo(name=s['name'], status=s['status'], cpu_percent=s['cpu'], memory_mb=s['mem']) for s in current_services]
                LAST_SERVICES_STATE = current_services

            errors_to_send = []
            if current_errors != LAST_ERRORS_STATE:
                errors_to_send = [agent_pb2.ErrorLog(log_message=err) for err in current_errors]
                LAST_ERRORS_STATE = current_errors

            metrics = agent_pb2.SystemMetrics(
                server_id=SERVER_ID,
                cpu_usage_percent=sys_cpu,
                memory_usage_percent=sys_mem,
                cf_tunnel_requests=cf_requests, # Cloudflare trafiği
                containers=containers_to_send,
                services=services_to_send,   
                error_logs=errors_to_send 
            )

            response = stub.PushMetrics(metrics)
            if response.success:
                print(f"[{SERVER_ID}] CF Istekleri: {cf_requests} | Container: {len(containers_to_send)}, Servis: {len(services_to_send)}, Log: {len(errors_to_send)}")
            
        except Exception as e:
             print(f"Ajan içi hata: {e}")
        
        time.sleep(5) 

def run():
    print(f"Merkeze bağlanılıyor...")
    with grpc.insecure_channel(SERVER_ADDRESS) as channel:
        stub = agent_pb2_grpc.ServerManagerStub(channel)
        push_metrics(stub)

if __name__ == '__main__':
    run()