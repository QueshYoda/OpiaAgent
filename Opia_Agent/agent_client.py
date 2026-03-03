import grpc
import psutil
import time
import docker
import subprocess
import urllib.request
import platform
import os

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

# Disk hızı ve Güncelleme kontrolü için zamanlayıcılar
LAST_DISK_IO = None
LAST_IO_TIME = time.time()
LAST_UPDATE_CHECK_TIME = 0
PENDING_UPDATES_COUNT = 0

try:
    docker_client = docker.from_env()
except Exception:
    docker_client = None

def get_pending_updates():
    """Fedora DNF üzerinden bekleyen güncellemeleri saatte 1 kez sayar."""
    global LAST_UPDATE_CHECK_TIME, PENDING_UPDATES_COUNT
    if time.time() - LAST_UPDATE_CHECK_TIME > 3600:
        try:
            res = subprocess.run(['dnf', 'check-update', '-q'], capture_output=True, text=True)
            PENDING_UPDATES_COUNT = len([line for line in res.stdout.split('\n') if line.strip()])
        except Exception:
            pass
        LAST_UPDATE_CHECK_TIME = time.time()
    return PENDING_UPDATES_COUNT

def get_cpu_temp():
    """İşlemci sıcaklığını okur."""
    try:
        temps = psutil.sensors_temperatures()
        if not temps: return 0.0
        for name, entries in temps.items():
            if name in ['coretemp', 'k10temp', 'cpu_thermal', 'acpitz']:
                return entries[0].current
        return list(temps.values())[0][0].current
    except Exception:
        return 0.0

def get_cpu_model():
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if 'model name' in line:
                    return line.split(':')[1].strip()
    except Exception:
        pass
    return platform.processor()

def get_disk_metrics():
    global LAST_DISK_IO, LAST_IO_TIME
    current_time = time.time()
    time_diff = current_time - LAST_IO_TIME
    if time_diff <= 0: time_diff = 1

    io_counters = psutil.disk_io_counters(perdisk=True)
    disks = []
    
    for part in psutil.disk_partitions(all=False):
        if 'loop' in part.device or 'snap' in part.mountpoint: continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            
            # Inode Hesabı
            try:
                st = os.statvfs(part.mountpoint)
                inode_percent = ((st.f_files - st.f_ffree) / st.f_files) * 100.0 if st.f_files > 0 else 0.0
            except:
                inode_percent = 0.0

            # IO (Okuma/Yazma) Hızı Hesabı
            read_speed = 0.0
            write_speed = 0.0
            dev_name = part.device.split('/')[-1]
            if io_counters and dev_name in io_counters:
                curr_io = io_counters[dev_name]
                if LAST_DISK_IO and dev_name in LAST_DISK_IO:
                    prev_io = LAST_DISK_IO[dev_name]
                    read_speed = ((curr_io.read_bytes - prev_io.read_bytes) / time_diff) / (1024*1024)
                    write_speed = ((curr_io.write_bytes - prev_io.write_bytes) / time_diff) / (1024*1024)

            disks.append({
                'device': part.device,
                'mount_point': part.mountpoint,
                'fstype': part.fstype,
                'total': round(usage.total / (1024**3), 2),
                'used': round(usage.used / (1024**3), 2),
                'free': round(usage.free / (1024**3), 2),
                'percent': usage.percent,
                'inode_percent': round(inode_percent, 2),
                'read_speed': round(max(0, read_speed), 2),
                'write_speed': round(max(0, write_speed), 2)
            })
        except Exception:
            continue
            
    LAST_DISK_IO = io_counters
    LAST_IO_TIME = current_time
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
    except Exception: pass
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
    except Exception: pass
    return metrics

def get_recent_errors():
    try:
        result = subprocess.run(['journalctl', '-p', '3', '-n', '5', '--no-pager'], capture_output=True, text=True)
        return [line[:200] for line in result.stdout.strip().split('\n') if line]
    except Exception as e:
        return [f"Log hatasi: {e}"]

def push_metrics(stub):
    global LAST_SERVICES_STATE, LAST_ERRORS_STATE, LAST_CONTAINERS_STATE, LAST_CLOUDFLARE_STATE, LAST_DISK_STATE, LAST_OS_STATE, LAST_HW_STATE
    
    while True:
        try:
            sys_cpu = psutil.cpu_percent(interval=1)
            sys_mem = psutil.virtual_memory()
            swap_mem = psutil.swap_memory()
            load1, load5, load15 = os.getloadavg()
            try: cpu_freq = psutil.cpu_freq().current / 1000.0
            except: cpu_freq = 0.0

            current_os = {
                'os_name': platform.system(),
                'version': platform.release(),
                'kernel_version': platform.version(),
                'arch': platform.machine(),
                'uptime': int(time.time() - psutil.boot_time()),
                'load1': round(load1, 2), 'load5': round(load5, 2), 'load15': round(load15, 2),
                'active_users': len(psutil.users()),
                'pending_updates': get_pending_updates()
            }

            current_hw = {
                'cpu_model': get_cpu_model(),
                'cpu_cores': psutil.cpu_count(logical=False) or 0,
                'cpu_logical': psutil.cpu_count(logical=True) or 0,
                'total_ram': round(sys_mem.total / (1024**3), 2),
                'cpu_temp': get_cpu_temp(),
                'cpu_freq': round(cpu_freq, 2),
                'swap_total': round(swap_mem.total / (1024**3), 2),
                'swap_used': round(swap_mem.used / (1024**3), 2),
                'swap_percent': swap_mem.percent
            }

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
                        sys_cpu_usage = stats['cpu_stats'].get('system_cpu_usage')
                        pre_sys_cpu = stats['precpu_stats'].get('system_cpu_usage')
                        if sys_cpu_usage and pre_sys_cpu:
                            sys_delta = sys_cpu_usage - pre_sys_cpu
                            if sys_delta > 0.0 and cpu_delta > 0.0:
                                online_cpus = stats['cpu_stats'].get('online_cpus') or len(stats['cpu_stats']['cpu_usage'].get('percpu_usage', [1]))
                                cpu_perc = (cpu_delta / sys_delta) * online_cpus * 100.0
                        mem_mb = stats['memory_stats']['usage'] / (1024 * 1024)

                    current_containers.append({
                        'id': c.short_id, 'name': c.name, 'state': c.status, 'health': health,
                        'cpu': round(cpu_perc, 2), 'mem': round(mem_mb, 2)
                    })

            current_services = [{'name': srv, 'status': st, 'cpu': c, 'mem': m} for srv in TARGET_SERVICES for st, c, m in [get_service_metrics(srv)]]
            current_errors = get_recent_errors()
            cf_data = get_cloudflare_metrics()

            metrics = agent_pb2.SystemMetrics(
                server_id=SERVER_ID,
                cpu_usage_percent=sys_cpu,
                memory_usage_percent=sys_mem.percent,
            )

            os_compare_state = {k: v for k, v in current_os.items() if k not in ['uptime', 'load1', 'load5', 'load15']}
            if os_compare_state != LAST_OS_STATE:
                metrics.os_info.CopyFrom(agent_pb2.OsInfo(os_name=current_os['os_name'], version=current_os['version'], kernel_version=current_os['kernel_version'], architecture=current_os['arch'], system_uptime_seconds=current_os['uptime'], load_avg_1m=current_os['load1'], load_avg_5m=current_os['load5'], load_avg_15m=current_os['load15'], active_users=current_os['active_users'], pending_updates=current_os['pending_updates']))
                LAST_OS_STATE = os_compare_state

            hw_compare_state = {k: v for k, v in current_hw.items() if k not in ['cpu_temp', 'cpu_freq']}
            if hw_compare_state != LAST_HW_STATE:
                metrics.hardware_info.CopyFrom(agent_pb2.HardwareInfo(cpu_model=current_hw['cpu_model'], cpu_cores=current_hw['cpu_cores'], cpu_logical_cores=current_hw['cpu_logical'], total_ram_gb=current_hw['total_ram'], cpu_temperature=current_hw['cpu_temp'], current_cpu_freq_ghz=current_hw['cpu_freq'], swap_total_gb=current_hw['swap_total'], swap_used_gb=current_hw['swap_used'], swap_usage_percent=current_hw['swap_percent']))
                LAST_HW_STATE = hw_compare_state

            if current_disks != LAST_DISK_STATE:
                for d in current_disks: metrics.disks.add(device=d['device'], mount_point=d['mount_point'], file_system=d['fstype'], total_gb=d['total'], used_gb=d['used'], free_gb=d['free'], usage_percent=d['percent'], read_speed_mb=d['read_speed'], write_speed_mb=d['write_speed'], inode_usage_percent=d['inode_percent'])
                LAST_DISK_STATE = current_disks

            if current_containers != LAST_CONTAINERS_STATE:
                for c in current_containers: metrics.containers.add(container_id=c['id'], name=c['name'], state=c['state'], health=c['health'], cpu_percent=c['cpu'], memory_mb=c['mem'])
                LAST_CONTAINERS_STATE = current_containers

            if current_services != LAST_SERVICES_STATE:
                for s in current_services: metrics.services.add(name=s['name'], status=s['status'], cpu_percent=s['cpu'], memory_mb=s['mem'])
                LAST_SERVICES_STATE = current_services

            if current_errors != LAST_ERRORS_STATE:
                for err in current_errors: metrics.error_logs.add(log_message=err)
                LAST_ERRORS_STATE = current_errors

            if cf_data != LAST_CLOUDFLARE_STATE:
                metrics.cloudflare.CopyFrom(agent_pb2.CloudflareInfo(total_requests=cf_data['total'], successful_requests=cf_data['success'], server_errors=cf_data['error'], active_sessions=cf_data['sessions'], latency_ms=cf_data['latency'], bytes_received=cf_data['recv'], bytes_sent=cf_data['sent']))
                LAST_CLOUDFLARE_STATE = cf_data

            response = stub.PushMetrics(metrics)
            if response.success: print(f"[{SERVER_ID}] CPU Sıcaklık: {current_hw['cpu_temp']}°C | OS Yük: {current_os['load1']} | İletildi.")
            
        except Exception as e: print(f"Ajan içi hata: {e}")
        time.sleep(5) 

def run():
    print(f"Merkeze bağlanılıyor...")
    with grpc.insecure_channel(SERVER_ADDRESS) as channel:
        stub = agent_pb2_grpc.ServerManagerStub(channel)
        push_metrics(stub)

if __name__ == '__main__':
    run()