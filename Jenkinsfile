stage('Ajanı Arka Planda Başlat') {
            steps {
                dir('Opia_Agent') {
                    script {
                        echo 'Eski ajan süreçleri temizleniyor...'
                        // pkill için sudo ekliyoruz ki root ile başlatılan eski ajanı kapatabilsin
                        sh "sudo pkill -f agent_client.py || true"

                        echo 'Yeni Opia Agent başlatılıyor...'
                        
                        // DİKKAT: FULL_PATH değişkenini çift tırnak içine aldık!
                        sh """
                        export BUILD_ID=dontKillMe
                        FULL_PATH=\$(pwd)
                        nohup sudo "\${FULL_PATH}/venv/bin/python" agent_client.py > agent_activity.log 2>&1 &
                        """
                        
                        sh "sleep 3"
                        
                        sh """
                        if pgrep -f agent_client.py > /dev/null; then
                            echo 'Opia Agent başarıyla arka planda çalışıyor.'
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