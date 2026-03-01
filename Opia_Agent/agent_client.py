import grpc
import psutil
import time
import docker
import subprocess # Yeni ekledik

import agent_pb2
import agent_pb2_grpc

SERVER_ADDRESS = '192.168.0.100:5054' # Senin Windows IP ve Portun
SERVER_ID = 'fedora-node-01'

# Docker daemon bağlantısı (varsa çalışır, yoksa hata vermemesi için try-except içine alabilirsin)
try:
    docker_client = docker.from_env()
except Exception:
    docker_client = None

# Gözlemlemek istediğimiz kritik servisler
TARGET_SERVICES = ['jenkins', 'postgresql']

def get_service_status(service_name):
    try:
        # systemctl is-active <servis_adi> komutunu çalıştırır
        result = subprocess.run(['systemctl', 'is-active', service_name], capture_output=True, text=True)
        return result.stdout.strip() # "active", "inactive" veya "failed" döner
    except Exception:
        return "unknown"

def get_recent_errors():
    error_logs = []
    try:
        # journalctl ile son 5 sistem hatasını (Priority 3 = Error) çekiyoruz
        result = subprocess.run(['journalctl', '-p', '3', '-n', '5', '--no-pager'], capture_output=True, text=True)
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if line:
                error_logs.append(agent_pb2.ErrorLog(log_message=line[:200])) # Çok uzunsa ilk 200 karakteri al
    except Exception as e:
        error_logs.append(agent_pb2.ErrorLog(log_message=f"Log okuma hatasi: {e}"))
    
    return error_logs

def push_metrics(stub):
    while True:
        try:
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            # --- Docker Verileri ---
            container_list = []
            if docker_client:
                containers = docker_client.containers.list(all=True)
                for c in containers:
                    health_status = "none"
                    if 'Health' in c.attrs['State']:
                        health_status = c.attrs['State']['Health']['Status']
                    container_list.append(agent_pb2.ContainerInfo(
                        container_id=c.short_id, name=c.name, state=c.status, health=health_status
                    ))

            # --- Servis Verileri ---
            service_list = []
            for srv in TARGET_SERVICES:
                status = get_service_status(srv)
                service_list.append(agent_pb2.ServiceInfo(name=srv, status=status))

            # --- Hata Logları ---
            recent_errors = get_recent_errors()

            # Protobuf nesnesini oluştur
            metrics = agent_pb2.SystemMetrics(
                server_id=SERVER_ID,
                cpu_usage_percent=cpu_usage,
                memory_usage_percent=memory.percent,
                containers=container_list,
                services=service_list,   # Yeni listemiz
                error_logs=recent_errors # Yeni listemiz
            )

            response = stub.PushMetrics(metrics)
            if response.success:
                print(f"[{SERVER_ID}] Veri iletildi! Servisler kontrol edildi, {len(recent_errors)} log gönderildi.")
            
        except grpc.RpcError as e:
             print(f"Bağlantı hatası: {e.details()}")
        except Exception as e:
             print(f"Ajan içi hata: {e}")
        
        time.sleep(5) 

def run():
    print(f"Merkeze ({SERVER_ADDRESS}) bağlanılıyor...")
    with grpc.insecure_channel(SERVER_ADDRESS) as channel:
        stub = agent_pb2_grpc.ServerManagerStub(channel)
        push_metrics(stub)

if __name__ == '__main__':
    run()