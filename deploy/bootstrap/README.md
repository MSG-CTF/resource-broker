# MSG Broker Node Agent Bootstrap

이 디렉터리의 공통 Bootstrap은 Ubuntu AMD64/ARM64 VM 하나를 다음 desired state로
맞춘다.

1. 지정한 버전의 single-node k3s 설치 또는 갱신
2. UID/GID `10001` 준비
3. VM 내부에서 EC P-256 private key와 CSR 생성
4. 수동 1회용 token 또는 Provider VM identity로 Agent client certificate 발급
5. Kubernetes Node에 Broker `resource_target_id` annotation 설정
6. RBAC과 digest 고정 Node Agent DaemonSet 적용
7. 실제 Broker observation 전송 확인

AWS/GCP/Azure가 달라지는 부분은 이 패키지를 VM에서 실행시키는 관리면이다.
VM 내부 설치 동작은 이 스크립트 하나를 공통으로 사용한다.

## 지원 범위

- Ubuntu Linux
- AMD64 (`x86_64`)
- ARM64 (`aarch64`, `arm64`)
- VM마다 독립된 single-node k3s
- systemd
- Docker Hub 또는 OCI registry의 digest 고정 Agent image

`INSTALL`과 `UPDATE`에 전달하는 Agent image는 `linux/amd64`와 `linux/arm64`를
모두 포함한 OCI image index의 digest여야 한다. 특정 아키텍처 manifest의 digest를
전달하면 다른 아키텍처 VM에서 image pull 또는 `exec format error`로 실패한다.

k3s는 기본 packaged component 설정으로 설치된다. 따라서 CoreDNS, Traefik,
ServiceLB, local-path-provisioner, metrics-server Pod가 보이는 것이 정상이다.
Bootstrap `remove`는 Runtime이 사용할 수 있는 k3s를 제거하지 않는다.

## 현재 검증 기준선

2026-08-10 깨끗한 GCP Ubuntu AMD64 VM에서 version `0.1.0` bundle을 사용해
다음을 실제로 확인했다.

- 외부 bundle `.sha256`과 내부 `SHA256SUMS` 검증
- 지정 버전 k3s 신규 설치와 systemd 기동
- VM-local EC P-256 private key/CSR 생성
- Admin이 발급한 VM별 1회용 token으로 certificate enrollment
- Node `resource_target_id` annotation과 RBAC 적용
- digest 고정 Node Agent DaemonSet rollout
- 중앙 Broker 최초 observation 전달
- 최종 `BOOTSTRAP_STATUS=ready`

이 검증은 공통 VM-local package의 수동 Canary다. 현재 GCP OS Policy, AWS SSM,
Azure Managed Run Command 무SSH 실행 adapter가 같은 package를 사용한다. Azure
경로는 구현됐지만 실제 Azure VM Canary는 아직 수행하지 않았다.

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
`expires_at`의 `Z`는 UTC를 뜻한다. 예를 들어 한국시간은 해당 시각에 9시간을
더해 해석하며, 만료된 token은 다시 발급해야 한다.

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

### Provider 자동화 enrollment

Provider Bootstrap job은 별도 공개 경로를 사용한다.

- `POST /v1/agent/bootstrap-enrollments/{job_id}`
- GCP: 정확한 audience로 발급된 VM identity JWT를 Bearer header로 제출
- AWS: IMDSv2의 instance identity document와 base64 RSA signature를 request
  body에 제출하며 Bearer token은 사용하지 않음
- Azure: job 전용 10자리 nonce로 요청한 IMDS PKCS#7 attested document를 request
  body에 제출하며 Bearer token은 사용하지 않음

세 방식 모두 job이 `APPLYING` 또는 `RUNNING`이고 deadline 전이며 아직 한 번도
소비되지 않았을 때만 인증서를 발급한다. GCP는 project/instance/zone을, AWS는
AWS 공개키 서명과 account/Region/instance ID를 비교한다. Azure는 공개 인증서
체인, 서명, nonce, 유효시간을 검증하고 Subscription ID와 `vmId`를 DB resource
target과 비교한다. private key는 VM에서 생성되고 밖으로 나오지 않는다.

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

적용 후 인증서 없는 요청의 경로 정책은 다음처럼 확인한다.

```bash
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
  -X POST -H 'Content-Type: application/json' --data '{}' \
  https://agents.mjsec.kr/v1/agent/enrollments

curl -sS -o /dev/null -w 'HTTP %{http_code}\n' \
  -X POST -H 'Content-Type: application/json' --data '{}' \
  https://agents.mjsec.kr/v1/agent/observations
```

- enrollment의 `422`: client certificate 없이 Backend validation까지 도달
- observation의 `401`: client certificate가 없어 차단

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

```bash
sudo rm -f /run/msg-broker-enrollment-token
sudo bash deploy/bootstrap/node-agent-bootstrap.sh check
```

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
bash deploy/bootstrap/build-bundle.sh 0.4.1
```

생성되는 `dist/*.tar.gz`와 `.sha256`은 artifact 저장소에 올린다. AWS SSM, GCP
OS Policy, Azure Managed Run Command가 checksum 검증 후 같은 bundle을 실행한다.
개인 SSH/SCP는 운영 배포 경로가 아니다.

## k3s installer pin 갱신

Bootstrap은 `get.k3s.io`에서 받은 installer를 root로 실행하기 전에
`MSG_BROKER_K3S_INSTALL_SHA256`과 대조한다. 기본 URL과 digest는
`node-agent-bootstrap.sh`에 함께 고정되어 있으므로 upstream installer가 바뀌면
설치는 fail-closed로 중단된다.

installer 내용을 의도적으로 갱신할 때는 신뢰할 수 있는 환경에서 새 파일의
SHA-256을 별도로 확인한 뒤 digest를 함께 갱신한다. mirror URL을 사용하더라도
고정 digest와 동일한 installer만 실행되며, 내용이 다르면 설치가 중단된다.

## Broker 저장과 Runtime pull

k3s는 최초 기동 시 `/etc/rancher/k3s/k3s.yaml`에 `system:admin`/
`system:masters` 관리자 kubeconfig를 자동 생성한다. Provider Bootstrap의
`install`과 `update`는 이 파일의 loopback API 주소를 inventory의 해당 VM 주소로
바꾼 임시 복사본을 만들고 Broker에 업로드한다. 원본 kubeconfig는 수정하지 않는다.

Broker는 VM별 최신 kubeconfig 한 건을 `k3s_credentials`에 저장한다. kubeconfig
원문은 DB에 넣지 않고 별도 secret에서 파생한 Fernet key로 암호화하며, SHA-256,
client certificate fingerprint와 만료 시각, source Bootstrap job을 함께 기록한다.

Broker 컨테이너에는 다음 설정이 필요하다.

```text
K3S_CREDENTIAL_UPLOAD_TOKEN_SECRET=<bootstrap-upload-secret-32-bytes-minimum>
K3S_CREDENTIAL_ENCRYPTION_SECRET=<database-encryption-secret-32-bytes-minimum>
K3S_ADDRESS_SOURCE=public_ip
RUNTIME_API_TOKEN_SECRET=<runtime-api-secret-32-bytes-minimum>
RUNTIME_API_TOKEN_SUBJECT=runtime
```

세 secret은 서로 다른 값이어야 한다. `K3S_ADDRESS_SOURCE`는 Runtime과 대상 VM의
네트워크에 맞춰 `public_ip` 또는 `private_ip`를 선택한다. 필수 storage 설정이나
해당 IP가 없으면 `install/update` job은 Provider 원격 실행 전에 각각 `503` 또는
`422`로 거절된다.

Provider runner는 다음 값을 Bootstrap에 자동 전달한다.

```text
MSG_BROKER_BOOTSTRAP_JOB_ID=<bootstrap-job-uuid>
MSG_BROKER_K3S_CREDENTIAL_UPLOAD_URL=https://agents.mjsec.kr/v1/agent/bootstrap-jobs/<job-id>/k3s-credentials
MSG_BROKER_K3S_SERVER_URL=https://<vm-runtime-address>:6443
MSG_BROKER_K3S_CREDENTIAL_UPLOAD_TOKEN_FILE=/absolute/path/upload-token
MSG_BROKER_K3S_CREDENTIAL_UPLOAD_REQUIRED=true
```

전송 payload는 다음 JSON이다. `kubeconfig_base64`는 JSON 표현을 위한 인코딩이며
암호화가 아니다. 전송 중 보호는 HTTPS, 저장 보호는 Broker의 암호화 secret이
담당한다.

```json
{
  "schema_version": 1,
  "bootstrap_job_id": "<bootstrap-job-uuid>",
  "resource_target_id": "<resource-target-uuid>",
  "server_url": "https://<vm-runtime-address>:6443",
  "kubeconfig_base64": "<base64-kubeconfig-yaml>",
  "client_certificate_fingerprint_sha256": "<64-lowercase-hex>",
  "client_certificate_not_after": "<UTC-timestamp>",
  "generated_at": "<UTC-timestamp>"
}
```

업로드 Bearer는 resource target, Bootstrap job, k3s server URL에 묶인 24시간
HS256 JWT다. Broker는 job이 `APPLYING/RUNNING`이고 deadline 전인지 확인하고,
관리자 certificate·private key·CA·fingerprint를 검증한 뒤 저장한다. Bootstrap의
임시 JSON과 token 파일은 mode `0600`이며 완료 시 삭제된다.

Runtime은 다음 API로 저장된 credential을 pull한다.

```http
GET /v1/runtime/k3s-credentials?limit=100&after=<optional-resource-target-uuid>
GET /v1/runtime/k3s-credentials/{resource_target_id}
Authorization: Bearer <RUNTIME_JWT>
```

Gateway를 통할 때 외부 경로는 `/api/v1/runtime/k3s-credentials`다. Runtime JWT는
HS256이며 `iss=msg-runtime`, `aud=msg-broker-k3s-credentials`,
`type=runtime_access`, `sub=RUNTIME_API_TOKEN_SUBJECT`를 사용한다. `iat`, `nbf`,
`exp`, `jti`가 필수이고 최대 수명은 300초다. 목록 응답은 최대 200개씩 페이지로
반환하며, Runtime은 `next_cursor`가 없어질 때까지 반복한다.

Runtime은 반환된 `kubeconfig_base64`를 디코드해 사용하며 대상 VM의 `6443/tcp`에
도달할 수 있어야 한다. 해당 IP가 k3s serving certificate SAN에 포함되는지와
Cloud firewall source 범위는 실제 Canary에서 확인해야 한다. 관리자 certificate가
갱신되면 새 `UPDATE` job이 동일 VM의 저장 row를 교체한다.
