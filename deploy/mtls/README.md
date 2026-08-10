# Node Agent mTLS 운영 절차

`agents.mjsec.kr`는 별도 서버가 아니라 중앙 Broker VM의 Agent 전용 mTLS
endpoint다. 각 대상 VM의 Node Agent는 자기 `resource_target_id`가 CN인 client
certificate로 이 endpoint에 접속한다.

2026-08-10 중앙 Broker VM에 이 구성을 적용해 인증서 없는 enrollment 요청은
Backend validation까지 도달하고, 인증서 없는 observation 요청은 `401`로
차단되는 것을 확인했다. 이어서 깨끗한 GCP Ubuntu AMD64 VM에서 Bootstrap이
VM-local CSR로 certificate를 발급받아 최초 observation을 전달했다.

## 1. 중앙 Broker VM에서 Agent CA 초기화

초기화 script는 legacy 수동 Root CA와 API가 사용할 별도 online enrollment CA를
분리한다. 두 CA는 Nginx trust bundle에 함께 들어가지만 private key는 공유하지
않는다.
Root private key는 중앙 Broker VM의 root 전용 경로에만 두고 Docker에
마운트하지 않는다. Backend에는 `enrollment-ca` 하위 디렉터리만 마운트한다.

```bash
sudo bash ./deploy/mtls/init-agent-ca.sh
```

기본 경로:

```text
/etc/msg-broker/pki/agent-ca
/etc/msg-broker/pki/agent-ca/enrollment-ca
```

## 2. 호스트 Nginx mTLS 설정 적용

먼저 enrollment CA mount가 포함되도록 Compose를 다시 빌드한다.

```bash
sudo docker compose \
  -f compose.yaml \
  -f compose.agent-enrollment.yaml \
  up -d --build
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

최초 enrollment는 아직 client certificate가 없으므로 Agent hostname은 TLS
client certificate를 선택적으로 요청한다. observation 경로는 Nginx location에서
검증 성공 certificate를 별도로 강제한다. 인증서 없이 호출하면 `401`이어야 한다.

```bash
curl https://agents.mjsec.kr/v1/agent/observations
```

## 3. 자동 enrollment

운영동형 설치는 `deploy/bootstrap/node-agent-bootstrap.sh`를 사용한다. VM에서
private key와 CSR을 만들고 Admin이 발급한 1회용 token으로 certificate만 받는다.
전체 API와 실행 방법은 `deploy/bootstrap/README.md`에 정리돼 있다.

공통 Bootstrap의 실제 Canary 검증은 완료됐지만 token/bundle을 사람이 SSH/SCP로
옮긴 과정은 운영 배포 방식이 아니다. 다음 단계의 Provider Bootstrap Runner가
이 전달과 실행을 맡아야 하며 token 원문을 DB나 일반 job log에 남기면 안 된다.

아래 수동 bundle 방식은 기존 Canary 진단과 이전 인증서 호환에만 사용한다.

## 4. Legacy Canary VM 인증서 발급

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

## 5. Legacy bundle을 대상 Canary VM에 설치

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

## 6. Legacy Canary Agent 전송 활성화

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

## 7. 인증서 폐기

중앙 Broker VM에서 실행한다.

```bash
sudo bash ./deploy/mtls/revoke-agent-certificate.sh <RESOURCE_TARGET_ID>
sudo nginx -t
sudo systemctl reload nginx
```

현재 `revoke-agent-certificate.sh`는 legacy 수동 Root CA bundle을 폐기한다.
enrollment CA 인증서의 중앙 폐기 API와 자동 Nginx reload는 후속 lifecycle
카드다. Bootstrap `remove`는 local key/certificate를 제거하고 중앙 폐기가
필요한 serial을 출력한다.
