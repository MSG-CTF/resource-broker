# MSG Broker Node Agent Bootstrap

이 디렉터리의 공통 Bootstrap은 Ubuntu AMD64 VM 하나를 다음 desired state로
맞춘다.

1. 지정한 버전의 single-node k3s 설치 또는 갱신
2. UID/GID `10001` 준비
3. VM 내부에서 EC P-256 private key와 CSR 생성
4. 1회용 enrollment token으로 Agent client certificate 발급
5. Kubernetes Node에 Broker `resource_target_id` annotation 설정
6. RBAC과 digest 고정 Node Agent DaemonSet 적용
7. 실제 Broker observation 전송 확인

AWS/GCP/Azure가 달라지는 부분은 이 패키지를 VM에서 실행시키는 관리면이다.
VM 내부 설치 동작은 이 스크립트 하나를 공통으로 사용한다.

## 지원 범위

- Ubuntu Linux
- AMD64 (`x86_64`)
- VM마다 독립된 single-node k3s
- systemd
- Docker Hub 또는 OCI registry의 digest 고정 Agent image

k3s는 기본 packaged component 설정으로 설치된다. 따라서 CoreDNS, Traefik,
ServiceLB, local-path-provisioner, metrics-server Pod가 보이는 것이 정상이다.
Bootstrap `remove`는 Runtime이 사용할 수 있는 k3s를 제거하지 않는다.

## Enrollment API

### 1. Admin이 1회용 token 발급

- Method: `POST`
- Backend path: `/v1/admin/resource-targets/{resource_target_id}/enrollment-tokens`
- Gateway path: `/api/v1/admin/resource-targets/{resource_target_id}/enrollment-tokens`
- 인증: Admin Bearer JWT
- token 기본 만료: 600초
- 같은 VM의 이전 미사용 token: 새 token 발급 시 폐기

Request headers:

```http
Accept: application/json
Content-Type: application/json
Authorization: Bearer <ADMIN_TOKEN>
```

Request body:

```json
{
  "expires_in_seconds": 600
}
```

Success: `201 Created`

```json
{
  "enrollment_id": "<UUID>",
  "resource_target_id": "<RESOURCE_TARGET_UUID>",
  "token": "mbe_<ONE_TIME_SECRET>",
  "expires_at": "2026-08-05T00:10:00Z",
  "created_at": "2026-08-05T00:00:00Z"
}
```

token 원문은 이 응답에서 한 번만 반환한다. DB에는 SHA-256 hash만 저장한다.

주요 오류:

- `401 ADMIN_TOKEN_REQUIRED`, `INVALID_ADMIN_TOKEN`
- `404 RESOURCE_TARGET_NOT_FOUND`
- `409 RESOURCE_TARGET_RETIRED`
- `500 DATABASE_ERROR`
- `503 ADMIN_AUTH_NOT_CONFIGURED`

### 2. VM이 CSR을 certificate로 교환

- Method: `POST`
- URL: `https://agents.mjsec.kr/v1/agent/enrollments`
- 인증: 위에서 발급한 1회용 Bearer token
- client certificate: 최초 enrollment에는 없음

Request headers:

```http
Accept: application/json
Content-Type: application/json
Authorization: Bearer <ONE_TIME_ENROLLMENT_TOKEN>
```

Request body:

```json
{
  "resource_target_id": "<RESOURCE_TARGET_UUID>",
  "certificate_signing_request_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...\n-----END CERTIFICATE REQUEST-----\n"
}
```

Success: `200 OK`

```json
{
  "resource_target_id": "<RESOURCE_TARGET_UUID>",
  "client_certificate_pem": "-----BEGIN CERTIFICATE-----\n...",
  "client_ca_pem": "-----BEGIN CERTIFICATE-----\n...",
  "serial_number": "2000",
  "fingerprint_sha256": "<64_HEX_CHARACTERS>",
  "not_before": "2026-08-05T00:00:00Z",
  "not_after": "2026-10-19T00:00:00Z",
  "issued_at": "2026-08-05T00:00:00Z"
}
```

주요 오류:

- `400 INVALID_AGENT_CSR`
- `401 ENROLLMENT_TOKEN_REQUIRED`, `INVALID_ENROLLMENT_TOKEN`
- `403 ENROLLMENT_RESOURCE_TARGET_MISMATCH`
- `404 RESOURCE_TARGET_NOT_FOUND`
- `409 RESOURCE_TARGET_RETIRED`
- `429`: enrollment gateway rate limit
- `500 AGENT_CERTIFICATE_ISSUANCE_FAILED`, `DATABASE_ERROR`
- `503 AGENT_ENROLLMENT_NOT_CONFIGURED`

CSR은 정확한 `resource_target_id` CN과 EC P-256 public key만 허용한다. CSR의
extension은 복사하지 않는다. private key는 VM의 `${TLS_DIR:-/etc/msg-broker-agent/tls}`
밖으로 나오지 않는다.

## 중앙 Broker 준비

테스트/운영 환경별 Agent Root CA를 초기화한다. 기존 Root CA가 있으면 보존하고
별도 온라인 enrollment CA만 추가한다.

```bash
sudo bash deploy/mtls/init-agent-ca.sh
```

enrollment CA를 Backend에만 마운트하는 Compose override를 포함해 실행한다.

```bash
sudo docker compose \
  -f compose.yaml \
  -f compose.agent-enrollment.yaml \
  up -d --build
```

그 다음 `deploy/nginx/msg-broker.conf`를 호스트 Nginx에 적용하고 문법 검사 후
reload한다. Root CA private key는 Backend에 마운트하지 않는다.

## Bootstrap 실행

token은 root만 읽을 수 있는 임시 파일로 전달한다. 명령행 인자나 일반 로그에
token 원문을 넣지 않는다.

```bash
sudo install -o root -g root -m 0600 /dev/null /run/msg-broker-enrollment-token
sudoedit /run/msg-broker-enrollment-token

sudo env \
  MSG_BROKER_RESOURCE_TARGET_ID=<RESOURCE_TARGET_UUID> \
  MSG_BROKER_K3S_VERSION=<PINNED_K3S_VERSION> \
  MSG_BROKER_AGENT_IMAGE='gonas0919/msg-broker-node-agent@sha256:<DIGEST>' \
  MSG_BROKER_ENROLLMENT_TOKEN_FILE=/run/msg-broker-enrollment-token \
  bash deploy/bootstrap/node-agent-bootstrap.sh install
```

성공한 token은 다시 사용할 수 없다. 실행 후 임시 token 파일은 배포 관리면이
제거해야 한다.

반복 실행:

```bash
sudo env <동일한 non-secret 설정> \
  bash deploy/bootstrap/node-agent-bootstrap.sh update

sudo bash deploy/bootstrap/node-agent-bootstrap.sh check

sudo bash deploy/bootstrap/node-agent-bootstrap.sh remove
```

- 인증서가 갱신 기간보다 충분히 남아 있으면 `install/update`에 token이 필요 없다.
- 인증서가 없거나 7일 안에 만료되면 새 token 파일이 필요하다.
- `check`는 최근 10분 이내 `Delivered observation` 로그까지 검사한다.
- `remove`는 Agent와 local certificate만 제거한다. k3s와 Runtime workload는
  보존하며, 출력된 serial의 중앙 폐기는 별도 Bootstrap Runner가 수행해야 한다.

## 배포 bundle

Provider 관리면에 전달할 버전 고정 bundle과 checksum을 만든다.

```bash
bash deploy/bootstrap/build-bundle.sh 0.1.0
```

생성되는 `dist/*.tar.gz`와 `.sha256`은 artifact 저장소에 올린다. AWS SSM,
Azure Managed Run Command, GCP OS Policy는 checksum 검증 후 같은 bundle의
스크립트를 실행해야 한다. 개인 SSH/SCP는 운영 배포 경로가 아니다.
