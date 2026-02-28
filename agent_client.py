import grpc
import psutil
import time

# Otomatik üretilen dosyalar
import agent_pb2
import agent_pb2_grpc

# İleride burası 'opia-agents.bahademirtas.com' 
SERVER_ADDRESS = '192.168.0.100:50051'
SERVER_ID = 'fedora-node-01' # Her sunucuya özel bir kimlik vereceğiz

def push_metrics(stub):
    while True:
        try:
            # Metrikleri topla
            cpu_usage = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()

            # Protobuf formatına çevir
            metrics = agent_pb2.SystemMetrics(
                server_id=SERVER_ID,
                cpu_usage_percent=cpu_usage,
                memory_usage_percent=memory.percent
            )

            # Merkeze gönder (Push)
            response = stub.PushMetrics(metrics)
            
            if response.success:
                print(f"[{SERVER_ID}] Metrikler merkeze iletildi -> CPU: %{cpu_usage}, RAM: %{memory.percent}")
            
        except grpc.RpcError as e:
            print(f"Merkezle bağlantı kurulamadı. Tekrar deneniyor... Hata: {e.details()}")
        
        # 5 saniyede bir gönder (Canlı izleme için)
        time.sleep(5) 

def run():
    print(f"Merkeze ({SERVER_ADDRESS}) bağlanılıyor...")
    # Güvenli olmayan (TLS'siz) test kanalı oluşturuyoruz
    with grpc.insecure_channel(SERVER_ADDRESS) as channel:
        stub = agent_pb2_grpc.ServerManagerStub(channel)
        push_metrics(stub)

if __name__ == '__main__':
    run()