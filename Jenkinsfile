pipeline {
    agent any

    environment {
        // Tam dosya yolu (WORKSPACE) yerine göreceli yol kullanıyoruz
        // Bu sayede job ismindeki boşluklar sorun yaratmaz
        VENV_DIR    = 'venv'
        PYTHON_BIN  = 'venv/bin/python'
        PIP_BIN     = 'venv/bin/pip'
    }

    stages {
        stage('Sanal Ortam Hazırlığı') {
            steps {
                // Kodların bulunduğu alt klasöre giriyoruz
                dir('Opia_Agent') {
                    script {
                        sh """
                        if [ ! -d "${VENV_DIR}" ]; then
                            python3 -m venv ${VENV_DIR}
                        fi
                        """
                        // Göreceli yollarla komutları çalıştırıyoruz
                        sh "./${PIP_BIN} install -r requirements.txt"
                    }
                }
            }
        }

        stage('gRPC Prototip Derleme') {
            steps {
                dir('Opia_Agent') {
                    sh "./${PYTHON_BIN} -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. agent.proto"
                }
            }
        }

        stage('Ajanı Arka Planda Başlat') {
            steps {
                dir('Opia_Agent') {
                    script {
                        echo 'Eski ajan süreçleri temizleniyor...'
                        sh "pkill -f agent_client.py || true"

                        echo 'Yeni Opia Agent başlatılıyor...'
                        
                        sh """
                        export BUILD_ID=dontKillMe
                        nohup sudo ./${PYTHON_BIN} agent_client.py > agent_activity.log 2>&1 &
                        """
                        
                        sh "sleep 2"
                        sh "pgrep -f agent_client.py && echo 'Opia Agent başarıyla arka planda çalışıyor.'"
                    }
                }
            }
        }
    }

    post {
        always {
            echo 'Pipeline tamamlandı.'
        }
    }
}