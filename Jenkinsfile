pipeline {
    agent any

    environment {
        // Sanal ortam için kullanılacak göreceli yollar
        VENV_DIR    = 'venv'
        PYTHON_BIN  = 'venv/bin/python'
        PIP_BIN     = 'venv/bin/pip'
    }

    stages {
        stage('Sanal Ortam Hazırlığı') {
            steps {
                dir('Opia_Agent') {
                    script {
                        echo 'Sanal ortam kontrol ediliyor...'
                        sh """
                        if [ ! -d "${VENV_DIR}" ]; then
                            python3 -m venv ${VENV_DIR}
                        fi
                        """
                        echo 'Bağımlılıklar yükleniyor...'
                        sh "./${PIP_BIN} install -r requirements.txt"
                    }
                }
            }
        }

        stage('gRPC Prototip Derleme') {
            steps {
                dir('Opia_Agent') {
                    echo 'gRPC dosyaları derleniyor...'
                    sh "./${PYTHON_BIN} -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. agent.proto"
                }
            }
        }

        stage('Ajanı Arka Planda Başlat') {
            steps {
                dir('Opia_Agent') {
                    script {
                        echo 'Eski ajan süreçleri temizleniyor...'
                        // Kapanmayan eski root süreçleri için sudo pkill kullanıyoruz
                        sh "sudo pkill -f agent_client.py || true"

                        echo 'Yeni Opia Agent başlatılıyor...'
                        
                        // FULL_PATH ile mutlak yolu alıp tırnak içine alarak "boşluk" sorununu çözüyoruz
                        sh """
                        export BUILD_ID=dontKillMe
                        FULL_PATH=\$(pwd)
                        nohup sudo "\${FULL_PATH}/venv/bin/python" agent_client.py > agent_activity.log 2>&1 &
                        """
                        
                        // Ajanın bağlantı kurması ve log yazması için 3 saniye bekle
                        sh "sleep 3"
                        
                        // Sürecin gerçekten çalışıp çalışmadığını kontrol et
                        sh """
                        if pgrep -f agent_client.py > /dev/null; then
                            echo '##################################################'
                            echo '   BAŞARILI: Opia Agent arka planda çalışıyor.'
                            echo '##################################################'
                        else
                            echo '##################################################'
                            echo '   HATA: Opia Agent başlatılamadı! LOG ÇIKTISI:'
                            echo '##################################################'
                            cat agent_activity.log || echo 'Log dosyası okunamadı.'
                            echo '##################################################'
                            exit 1
                        fi
                        """
                    }
                }
            }
        }
    }

    post {
        always {
            echo 'Pipeline işlemi sonlandı.'
        }
    }
}