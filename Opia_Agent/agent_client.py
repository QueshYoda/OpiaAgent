import grpc
import psutil
import time
import docker

import agent_pb2
import agent_pb2_grpc

SERVER_ADDRESS = '192.168.0.100:50051' # .NET sunucusunun adresi
SERVER_ID = 'fedora-node-01'

# Docker daemon'una bağlan (Arka planda /var/run/docker.sock kullanır)
docker_client = docker.from_env()

def push_metrics(stub):
    while True:
        try:
            # Temel sistem metrikleri
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            # Docker container'larını çek
            container_list = []
            containers = docker_client.containers.list(all=True) # Duranları da görmek için all=True
            
            for c in containers:
                # Eğer container'da Healthcheck tanımlıysa durumunu al, yoksa "none" dön
                health_status = "none"
                if 'Health' in c.attrs['State']:
                    health_status = c.attrs['State']['Health']['Status']

                container_info = agent_pb2.ContainerInfo(
                    container_id=c.short_id,
                    name=c.name,
                    state=c.status,
                    health=health_status
                )
                container_list.append(container_info)

            # Protobuf formatına çevir ve container dizisini ekle
            metrics = agent_pb2.SystemMetrics(
                server_id=SERVER_ID,
                cpu_usage_percent=cpu_usage,
                memory_usage_percent=memory.percent,
                containers=container_list # Listeyi buraya veriyoruz
            )

            # Merkeze gönder
            response = stub.PushMetrics(metrics)
            
            if response.success:
                print(f"[{SERVER_ID}] Veri iletildi! CPU: %{cpu_usage}, RAM: %{memory.percent}, Container Sayısı: {len(container_list)}")
            
        except Exception as e:
            print(f"Hata oluştu veya Merkezle bağlantı kurulamadı: {e}")
        
        time.sleep(5) 

def run():
    print(f"Merkeze ({SERVER_ADDRESS}) bağlanılıyor...")
    with grpc.insecure_channel(SERVER_ADDRESS) as channel:
        stub = agent_pb2_grpc.ServerManagerStub(channel)
        push_metrics(stub)

if __name__ == '__main__':
    run()