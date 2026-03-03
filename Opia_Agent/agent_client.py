import grpc
import psutil
import time
import docker
import subprocess
import urllib.request
import platform

import agent_pb2
import agent_pb2_grpc

SERVER_ADDRESS = '192.168.0.100:5054' 
SERVER_ID = 'fedora-node-01'

TARGET_SERVICES = ['jenkins', 'postgresql', 'cloudflared'] 

LAST_SERVICES_STATE = []
LAST_ERRORS_STATE = []
LAST_CONTAINERS_STATE = []
LAST_CLOUDFLARE_STATE = None
LAST_DISK_STATE = []
LAST_OS_STATE = None
LAST_HW_STATE = None

try:
    docker_client = docker.from_env()
except Exception:
    docker_client = None

def get_cpu_model():
    """Linux tabanlı sistemlerde işlemci modelini tam olarak okur."""
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if 'model name' in line:
                    return line.split(':')[1].strip()
    except Exception:
        pass
    return platform.processor()

def get_disk_metrics():
    """Sistemdeki fiziksel disklerin durumunu okur."""
    disks = []
    # loop ve snap device'ları görmezden gelmek için filtreleme yapıyoruz
    for part in psutil.disk_partitions(all=False):
        if 'loop' in part.device or 'snap' in part.mountpoint:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                'device': part.device,
                'mount_point': part.mountpoint,
                'fstype': part.fstype,
                'total': round(usage.total / (1024**3), 2),
                'used': round(usage.used / (1024**3), 2),
                'free': round(usage.free / (1024**3), 2),
                'percent': usage.percent
            })
        except Exception:
            continue
    return disks

def get_service_metrics(service_name):
    status = "unknown"
    cpu = 0.0
    mem_mb = 0.0
    try:
        res_status = subprocess.run(['systemctl', 'is-active', service_name], capture_output=True, text=True)
        status = res_status.stdout.strip()
        if status == "active":
            res_pid = subprocess.run(['systemctl', 'show', '-p', 'MainPID', '--value', service_name], capture_output=True, text=True)
            pid_str = res_pid.stdout.strip()
            if pid_str and pid_str != '0':
                process = psutil.Process(int(pid_str))
                cpu = process.cpu_percent(interval=0.1)
                mem_mb = process.memory_info().rss / (1024 * 1024) 
    except Exception:
        pass
    return status, round(cpu, 2), round(mem_mb, 2)

def get_cloudflare_metrics():
    metrics = { 'total': 0.0, 'success': 0.0, 'error': 0.0, 'sessions': 0.0, 'latency': 0.0, 'recv': 0.0, 'sent': 0.0 }
    try:
        req = urllib.request.urlopen('http://localhost:2000/metrics', timeout=2)
        lines = req.read().decode('utf-8').split('\n')
        latencies = []
        for line in lines:
            if line.startswith('cloudflared_tunnel_total_requests '): metrics['total'] = float(line.split(' ')[1])
            elif line.startswith('cloudflared_tunnel_response_by_code{status_code="200"} '): metrics['success'] = float(line.split(' ')[1])
            elif line.startswith('cloudflared_tunnel_response_by_code{status_code="500"} '): metrics['error'] = float(line.split(' ')[1])
            elif line.startswith('cloudflared_tcp_active_sessions '): metrics['sessions'] = float(line.split(' ')[1])
            elif line.startswith('quic_client_smoothed_rtt{'): latencies.append(float(line.split(' ')[1]))
            elif line.startswith('quic_client_receive_bytes{'): metrics['recv'] += float(line.split(' ')[1]) 
            elif line.startswith('quic_client_sent_bytes{'): metrics['sent'] += float(line.split(' ')[1]) 
        if latencies: metrics['latency'] = round(sum(latencies) / len(latencies), 2)
    except Exception:
        pass
    return metrics

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
    global LAST_SERVICES_STATE, LAST_ERRORS_STATE, LAST_CONTAINERS_STATE, LAST_CLOUDFLARE_STATE, LAST_DISK_STATE, LAST_OS_STATE, LAST_HW_STATE
    
    while True:
        try:
            sys_cpu = psutil.cpu_percent(interval=1)
            sys_mem = psutil.virtual_memory()

            # --- VERİ TOPLAMA BÖLÜMÜ ---
            
            # Yeni 1: OS Bilgisi
            current_os = {
                'os_name': platform.system(),
                'version': platform.release(),
                'kernel_version': platform.version(),
                'arch': platform.machine(),
                'uptime': int(time.time() - psutil.boot_time())
            }

            # Yeni 2: Donanım Bilgisi
            current_hw = {
                'cpu_model': get_cpu_model(),
                'cpu_cores': psutil.cpu_count(logical=False) or 0,
                'cpu_logical': psutil.cpu_count(logical=True) or 0,
                'total_ram': round(sys_mem.total / (1024**3), 2)
            }

            # Yeni 3: Disk Verisi
            current_disks = get_disk_metrics()

            current_containers = []
            if docker_client:
                for c in docker_client.containers.list(all=True):
                    health = c.attrs['State']['Health']['Status'] if 'State' in c.attrs and 'Health' in c.attrs['State'] else "none"
                    cpu_perc = 0.0
                    mem_mb = 0.0
                    if c.status == 'running':
                        stats = c.stats(stream=False) 
                        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage'].get('total_usage', 0)
                        system_cpu_usage = stats['cpu_stats'].get('system_cpu_usage')
                        precpu_system_cpu_usage = stats['precpu_stats'].get('system_cpu_usage')
                        if system_cpu_usage is not None and precpu_system_cpu_usage is not None:
                            system_cpu_delta = system_cpu_usage - precpu_system_cpu_usage
                            if system_cpu_delta > 0.0 and cpu_delta > 0.0:
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

            current_services = [{'name': srv, 'status': st, 'cpu': c, 'mem': m} for srv in TARGET_SERVICES for st, c, m in [get_service_metrics(srv)]]
            current_errors = get_recent_errors()
            cf_data = get_cloudflare_metrics()

            # --- DELTA KONTROLÜ (Değişmeyenler Gönderilmez) ---
            
            metrics = agent_pb2.SystemMetrics(
                server_id=SERVER_ID,
                cpu_usage_percent=sys_cpu,
                memory_usage_percent=sys_mem.percent,
            )

            # OS Kontrolü (Sadece ilk çalışmada veya uptime 1 saati geçerse gönderilebilir ama şimdilik sadece değişimi yollayacağız)
            # Uptime sürekli değiştiği için OS_STATE kontrolünü uptime hariç yapalım ki DB şişmesin
            os_compare_state = {k: v for k, v in current_os.items() if k != 'uptime'}
            if os_compare_state != LAST_OS_STATE:
                metrics.os_info.CopyFrom(agent_pb2.OsInfo(os_name=current_os['os_name'], version=current_os['version'], kernel_version=current_os['kernel_version'], architecture=current_os['arch'], system_uptime_seconds=current_os['uptime']))
                LAST_OS_STATE = os_compare_state

            # Donanım Kontrolü
            if current_hw != LAST_HW_STATE:
                metrics.hardware_info.CopyFrom(agent_pb2.HardwareInfo(cpu_model=current_hw['cpu_model'], cpu_cores=current_hw['cpu_cores'], cpu_logical_cores=current_hw['cpu_logical'], total_ram_gb=current_hw['total_ram']))
                LAST_HW_STATE = current_hw

            # Disk Kontrolü
            if current_disks != LAST_DISK_STATE:
                for d in current_disks:
                    metrics.disks.add(device=d['device'], mount_point=d['mount_point'], file_system=d['fstype'], total_gb=d['total'], used_gb=d['used'], free_gb=d['free'], usage_percent=d['percent'])
                LAST_DISK_STATE = current_disks

            if current_containers != LAST_CONTAINERS_STATE:
                for c in current_containers:
                    metrics.containers.add(container_id=c['id'], name=c['name'], state=c['state'], health=c['health'], cpu_percent=c['cpu'], memory_mb=c['mem'])
                LAST_CONTAINERS_STATE = current_containers

            if current_services != LAST_SERVICES_STATE:
                for s in current_services:
                    metrics.services.add(name=s['name'], status=s['status'], cpu_percent=s['cpu'], memory_mb=s['mem'])
                LAST_SERVICES_STATE = current_services

            if current_errors != LAST_ERRORS_STATE:
                for err in current_errors:
                    metrics.error_logs.add(log_message=err)
                LAST_ERRORS_STATE = current_errors

            if cf_data != LAST_CLOUDFLARE_STATE:
                metrics.cloudflare.CopyFrom(agent_pb2.CloudflareInfo(total_requests=cf_data['total'], successful_requests=cf_data['success'], server_errors=cf_data['error'], active_sessions=cf_data['sessions'], latency_ms=cf_data['latency'], bytes_received=cf_data['recv'], bytes_sent=cf_data['sent']))
                LAST_CLOUDFLARE_STATE = cf_data

            # Sunucuya gönder
            response = stub.PushMetrics(metrics)
            if response.success:
                print(f"[{SERVER_ID}] CPU: %{sys_cpu} | Değişimler -> Disk: {len(metrics.disks)}, Container: {len(metrics.containers)}")
            
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