# Node Agent mTLS 운영 절차

`agents.mjsec.kr`는 별도 서버가 아니라 중앙 Broker VM의 Agent 전용 mTLS
endpoint다. 각 대상 VM의 Node Agent는 자기 `resource_target_id`가 CN인 client
certificate로 이 endpoint에 접속한다.

## 1. 중앙 Broker VM에서 Agent CA 초기화

CA private key는 중앙 Broker VM의 root 전용 경로에만 둔다. 저장소, Docker
image, 인증서 bundle 또는 대상 VM으로 복사하지 않는다.

```bash
sudo bash ./deploy/mtls/init-agent-ca.sh
```

기본 경로:

```text
/etc/msg-broker/pki/agent-ca
```

## 2. 호스트 Nginx mTLS 설정 적용

먼저 새 frontend Nginx 경로가 배포되도록 Compose를 다시 빌드한다.

```bash
sudo docker compose up -d --build
```

그다음 저장소의 gateway 설정을 호스트 Nginx에 복사한다.

```bash
sudo cp deploy/nginx/msg-broker.conf /etc/nginx/sites-available/msg-broker
sudo nginx -t
sudo systemctl reload nginx
```

Let's Encrypt 인증서가 갱신된 뒤 Nginx가 새 인증서를 읽도록 deploy hook도
설치한다.

```bash
sudo install -o root -g root -m 0755 \
  deploy/nginx/reload-nginx-after-renewal.sh \
  /etc/letsencrypt/renewal-hooks/deploy/reload-msg-broker-nginx
```

```bash
sudo certbot renew --dry-run --run-deploy-hooks
```

인증서 없이 Agent endpoint를 호출하면 TLS handshake에서 거절돼야 한다.

```bash
curl https://agents.mjsec.kr/v1/agent/observations
```

## 3. Canary VM 인증서 발급

Broker Admin에서 Canary VM의 `resource_target_id`를 확인한다.

```bash
sudo bash ./deploy/mtls/issue-agent-certificate.sh <RESOURCE_TARGET_ID>
```

bundle은 기본적으로 다음에 생성된다.

```text
/etc/msg-broker/pki/agent-ca/issued/<RESOURCE_TARGET_ID>
```

`client.key`는 민감 정보다. bundle을 대상 VM으로 옮길 때만 SSH/SCP 같은 암호화
경로를 사용하고, 전송 후 임시 복사본을 남기지 않는다.

## 4. 대상 Canary VM에 설치

bundle과 `install-agent-certificate.sh`를 Canary VM으로 안전하게 복사한 뒤 해당
VM에서 실행한다.

```bash
sudo bash ./install-agent-certificate.sh <BUNDLE_DIRECTORY>
```

설치 경로:

```text
/etc/msg-broker-agent/tls/client.crt
/etc/msg-broker-agent/tls/client.key
```

DaemonSet은 UID/GID `10001`로 실행되며 위 key만 읽을 수 있다.

## 5. Canary Agent 전송 활성화

Canary VM의 k3s Node annotation이 인증서 CN과 같은 `resource_target_id`인지
확인한다. 이미지 주소를 실제 값으로 바꾸고 dry-run을 해제한 뒤 배포한다.

```bash
sudo k3s kubectl -n msg-broker-system set env \
  daemonset/msg-broker-node-agent AGENT_DRY_RUN=false
```

```bash
sudo k3s kubectl -n msg-broker-system rollout restart \
  daemonset/msg-broker-node-agent
```

```bash
sudo k3s kubectl -n msg-broker-system logs \
  daemonset/msg-broker-node-agent --tail=100
```

## 6. 인증서 폐기

중앙 Broker VM에서 실행한다.

```bash
sudo bash ./deploy/mtls/revoke-agent-certificate.sh <RESOURCE_TARGET_ID>
sudo nginx -t
sudo systemctl reload nginx
```

폐기 후 해당 Agent의 TLS handshake가 거절되는지 확인한다.
