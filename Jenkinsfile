pipeline {
    agent any

    environment {
        // Sanal ortam yollarını Jenkins workspace'ine göre dinamik tanımlıyoruz
        VENV_DIR    = 'venv'
        PYTHON_BIN  = "${WORKSPACE}/venv/bin/python"
        PIP_BIN     = "${WORKSPACE}/venv/bin/pip"
    }

    stages {
        stage('Sanal Ortam Hazırlığı') {
            steps {
                script {
                    // Eğer venv klasörü yoksa oluştur, varsa geç
                    sh """
                    if [ ! -d "${VENV_DIR}" ]; then
                        python3 -m venv ${VENV_DIR}
                    fi
                    """
                    // Bağımlılıkları sanal ortama yükle
                    sh "${PIP_BIN} install -r requirements.txt"
                }
            }
        }

        stage('gRPC Prototip Derleme') {
            steps {
                // gRPC stub dosyalarını oluştur
                sh "${PYTHON_BIN} -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. agent.proto"
            }
        }

        stage('Ajanı Arka Planda Başlat') {
            steps {
                script {
                    echo 'Eski ajan süreçleri temizleniyor...'
                    // Eğer halihazırda çalışan eski bir süreç varsa durduruyoruz
                    sh "pkill -f agent_client.py || true"

                    echo 'Yeni Opia Agent başlatılıyor...'
                    
                    // Jenkins, pipeline bittiğinde başlattığı alt süreçleri otomatik öldürür.
                    // Bunu engellemek ve ajanın kalıcı olması için BUILD_ID'yi geçici olarak değiştiriyoruz.
                    sh """
                    export BUILD_ID=dontKillMe
                    nohup sudo ${PYTHON_BIN} agent_client.py > agent_activity.log 2>&1 &
                    """
                    
                    // Ajanın ayağa kalkması için 2 saniye bekleyip kontrol edelim
                    sh "sleep 2"
                    sh "pgrep -f agent_client.py && echo 'Opia Agent başarıyla arka planda çalışıyor.'"
                }
            }
        }
    }

    post {
        always {
            // Workspace temizliği yaparken venv ve üretilen logları koruyoruz
            echo 'Pipeline tamamlandı.'
        }
    }
}