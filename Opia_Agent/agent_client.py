import grpc
import psutil
import time
import docker
import subprocess

import agent_pb2
import agent_pb2_grpc

# Sunucu adresi ve Ajan kimliği (Kendi yapına göre kontrol et)
SERVER_ADDRESS = '192.168.0.100:5054' 
SERVER_ID = 'fedora-node-01'

# Gözlemlemek istediğimiz kritik servisler
TARGET_SERVICES = ['jenkins', 'postgresql']

# Ajanın hafızası (Sadece değişen verileri algılamak için önceki durumları tutar)
LAST_SERVICES_STATE = []
LAST_ERRORS_STATE = []
LAST_CONTAINERS_STATE = []

# Docker daemon bağlantısı (Eğer docker kapalıysa hata fırlatmaması için try-except)
try:
    docker_client = docker.from_env()
except Exception:
    docker_client = None

def get_service_status(service_name):
    """Belirtilen systemd servisinin durumunu döndürür."""
    try:
        # systemctl is-active komutunu çalıştır
        result = subprocess.run(['systemctl', 'is-active', service_name], capture_output=True, text=True)
        return result.stdout.strip() # "active", "inactive" veya "failed"
    except Exception:
        return "unknown"

def get_recent_errors():
    """Journalctl üzerinden son 5 sistem hatasını (Priority 3) metin listesi olarak döndürür."""
    current_errors = []
    try:
        result = subprocess.run(['journalctl', '-p', '3', '-n', '5', '--no-pager'], capture_output=True, text=True)
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if line:
                current_errors.append(line[:200]) # Log çok uzunsa ilk 200 karakteri alıyoruz
    except Exception as e:
        current_errors.append(f"Log okuma hatasi: {e}")
    
    return current_errors

def push_metrics(stub):
    global LAST_SERVICES_STATE, LAST_ERRORS_STATE, LAST_CONTAINERS_STATE
    
    while True:
        try:
            # 1. Temel Donanım Metrikleri (Her saniye değiştiği için her zaman gönderilir)
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            # 2. Container Verilerini Topla (Sözlük listesi olarak)
            current_containers = []
            if docker_client:
                try:
                    for c in docker_client.containers.list(all=True):
                        health = "none"
                        if 'State' in c.attrs and 'Health' in c.attrs['State']:
                            health = c.attrs['State']['Health']['Status']
                        
                        current_containers.append({
                            'id': c.short_id, 
                            'name': c.name, 
                            'state': c.status, 
                            'health': health
                        })
                except Exception as e:
                    print(f"Docker okuma hatası: {e}")

            # 3. Servis Verilerini Topla (Sözlük listesi olarak)
            current_services = [{'name': srv, 'status': get_service_status(srv)} for srv in TARGET_SERVICES]

            # 4. Log Verilerini Topla (Metin listesi olarak)
            current_errors = get_recent_errors()

            # --- DEĞİŞİM KONTROLÜ (DELTA MANTIĞI) ---
            
            containers_to_send = []
            if current_containers != LAST_CONTAINERS_STATE:
                containers_to_send = [
                    agent_pb2.ContainerInfo(container_id=c['id'], name=c['name'], state=c['state'], health=c['health']) 
                    for c in current_containers
                ]
                LAST_CONTAINERS_STATE = current_containers

            services_to_send = []
            if current_services != LAST_SERVICES_STATE:
                services_to_send = [
                    agent_pb2.ServiceInfo(name=s['name'], status=s['status']) 
                    for s in current_services
                ]
                LAST_SERVICES_STATE = current_services

            errors_to_send = []
            if current_errors != LAST_ERRORS_STATE:
                errors_to_send = [
                    agent_pb2.ErrorLog(log_message=err) 
                    for err in current_errors
                ]
                LAST_ERRORS_STATE = current_errors

            # Protobuf nesnesini oluştur (Değişim yoksa listeler ağa boş [] olarak gider)
            metrics = agent_pb2.SystemMetrics(
                server_id=SERVER_ID,
                cpu_usage_percent=cpu_usage,
                memory_usage_percent=memory.percent,
                containers=containers_to_send,
                services=services_to_send,   
                error_logs=errors_to_send 
            )

            # Sunucuya gönder
            response = stub.PushMetrics(metrics)
            if response.success:
                print(f"[{SERVER_ID}] CPU: %{cpu_usage} | RAM: %{memory.percent} | Değişimler -> Container: {len(containers_to_send)}, Servis: {len(services_to_send)}, Log: {len(errors_to_send)}")
            
        except grpc.RpcError as e:
             print(f"Bağlantı hatası: {e.details()}")
        except Exception as e:
             print(f"Ajan içi hata: {e}")
        
        # 5 saniye bekle ve döngüyü tekrar et
        time.sleep(5) 

def run():
    print(f"Merkeze ({SERVER_ADDRESS}) bağlanılıyor...")
    # HTTP/2 Cleartext üzerinden güvensiz bağlantı (yerel ağ için)
    with grpc.insecure_channel(SERVER_ADDRESS) as channel:
        stub = agent_pb2_grpc.ServerManagerStub(channel)
        push_metrics(stub)

if __name__ == '__main__':
    run()