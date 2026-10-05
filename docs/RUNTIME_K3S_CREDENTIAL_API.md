# Runtime k3s 인증정보 API

이 문서는 Runtime이 MSG Broker에서 대상 VM의 k3s kubeconfig를 가져와
Kubernetes API에 연결하는 계약을 설명한다. Postman과 Swagger Editor에 바로
가져올 수 있는 명세는
[`RUNTIME_K3S_CREDENTIAL_API.openapi.yaml`](./RUNTIME_K3S_CREDENTIAL_API.openapi.yaml)에
있다.

## 책임과 호출 흐름

Broker는 Bootstrap이 업로드한 VM별 최신 k3s 관리자 kubeconfig를 암호화해
저장한다. 단일 Runtime은 목록 API로 현재 사용 가능한 모든 VM의 인증정보를
가져오고, `resource_target_id`를 키로 각 Kubernetes 클라이언트를 관리한다.

```text
Runtime ── fixed Bearer token ──> Broker credential list API
        │                           │
        │                           └─ VM별 encrypted kubeconfig 복호화
        ▼
VM별 Base64 kubeconfig 디코드
        │
        ▼
resource_target_id별 https://<address>:6443 Kubernetes API client
```

Scheduler가 특정 VM에 workload를 배치할 때 사용하는 ID의 의미는 다음과 같다.

- `candidate_id`: Broker의 `resource_target_id`. 인증정보 API 경로에 사용한다.
- `target_id`: Kubernetes Node UID. 인증정보 API 경로에 사용하지 않는다.

## Base URL

- 외부 Gateway: `https://broker.mjsec.kr/api`
- Broker 앱 내부 주소를 직접 호출할 때: `/api` 접두사 없이 `/v1/...`

Runtime 운영 연동은 HTTPS Gateway 주소를 사용한다.

## 인증

Broker와 단일 Runtime에 동일한 고정 `RUNTIME_API_TOKEN`을 설정한다.

```env
RUNTIME_API_TOKEN=<32자 이상의 Runtime 전용 랜덤 토큰>
```

이 값은 Admin, Scheduler, Bootstrap 업로드, DB 암호화 secret과 달라야 한다.
로그인이나 토큰 발급 API는 없다. 모든 요청에 다음 헤더를 보낸다.

```http
Authorization: Bearer <RUNTIME_API_TOKEN>
Accept: application/json
```

토큰이 노출되면 Broker와 Runtime의 값을 함께 교체한다.

## 전체 인증정보 목록 조회

Runtime의 기본 동기화 API다. 등록된 모든 VM 중 k3s 인증정보가 존재하고,
후보로 활성화됐으며 retired 상태가 아닌 VM을 반환한다.

- Method: `GET`
- Gateway path: `/api/v1/runtime/k3s-credentials`
- Internal path: `/v1/runtime/k3s-credentials`
- Query:
  - `limit`: `1`~`200`, 기본값 `200`
  - `after`: 이전 응답의 `next_cursor`
- Request body: 없음
- Success: `200 OK`

```bash
curl --fail-with-body --silent --show-error \
  'https://broker.mjsec.kr/api/v1/runtime/k3s-credentials?limit=200' \
  --header 'Accept: application/json' \
  --header "Authorization: Bearer ${RUNTIME_API_TOKEN}"
```

응답:

```json
{
  "items": [
    {
      "schema_version": 1,
      "resource_target_id": "00000000-0000-4000-8000-000000000001",
      "source_bootstrap_job_id": "00000000-0000-4000-8000-000000000002",
      "server_url": "https://192.0.2.10:6443",
      "kubeconfig_base64": "<base64-kubeconfig-yaml>",
      "client_certificate_fingerprint_sha256": "<64-lowercase-hex>",
      "client_certificate_not_after": "2027-09-01T00:00:00Z",
      "generated_at": "2026-09-01T00:00:00Z",
      "uploaded_at": "2026-09-01T00:00:01Z"
    }
  ],
  "next_cursor": null
}
```

`kubeconfig_base64`는 JSON으로 안전하게 전달하기 위한 Base64 인코딩이다.
암호화가 아니므로 Runtime은 응답과 디코딩된 kubeconfig를 로그에 남기거나 공개
API로 반환하면 안 된다.

`next_cursor`가 `null`이 아니면 그 값을 다음 요청의 `after`에 넣는다. 현재 운영
규모가 200대 이하이면 한 번의 요청으로 전부 반환된다.

## 단일 VM 인증정보 조회

특정 VM 한 건만 다시 조회하거나 장애를 진단할 때 사용하는 보조 API다.

- Method: `GET`
- Gateway path: `/api/v1/runtime/k3s-credentials/{resource_target_id}`
- Internal path: `/v1/runtime/k3s-credentials/{resource_target_id}`
- Request body: 없음
- Success: `200 OK`

```bash
curl --fail-with-body --silent --show-error \
  'https://broker.mjsec.kr/api/v1/runtime/k3s-credentials/00000000-0000-4000-8000-000000000001' \
  --header 'Accept: application/json' \
  --header "Authorization: Bearer ${RUNTIME_API_TOKEN}"
```

## Python 연동 예시

실제 Runtime은 kubeconfig를 공유 평문 파일에 저장하지 않고 메모리에서
Kubernetes 클라이언트에 전달한다.

```python
import base64
import os

import requests
import yaml
from kubernetes import client, config


def load_kubernetes_clients() -> dict[str, client.ApiClient]:
    clients = {}
    after = None

    while True:
        params = {"limit": 200}
        if after is not None:
            params["after"] = after

        response = requests.get(
            "https://broker.mjsec.kr/api/v1/runtime/k3s-credentials",
            params=params,
            headers={
                "Authorization": f"Bearer {os.environ['RUNTIME_API_TOKEN']}",
                "Accept": "application/json",
            },
            timeout=10,
        )
        response.raise_for_status()
        page = response.json()

        for item in page["items"]:
            kubeconfig = yaml.safe_load(
                base64.b64decode(item["kubeconfig_base64"])
            )
            clients[item["resource_target_id"]] = (
                config.new_client_from_config_dict(kubeconfig)
            )

        after = page["next_cursor"]
        if after is None:
            return clients


api_clients = load_kubernetes_clients()
apps = client.AppsV1Api(
    api_clients["00000000-0000-4000-8000-000000000001"]
)
```

필요 패키지는 `requests`, `PyYAML`, `kubernetes`다. Runtime은 요청별 timeout을
설정하고 Broker 오류와 대상 k3s 네트워크 오류를 구분해야 한다.

## 오류

Broker 오류는 다음 형식을 사용한다.

```json
{
  "error": {
    "code": "STABLE_ERROR_CODE",
    "message": "Sanitized public message."
  }
}
```

| HTTP | code | 의미와 Runtime 처리 |
| --- | --- | --- |
| `401` | `RUNTIME_TOKEN_REQUIRED` | Bearer 헤더 누락. Runtime 설정 오류로 처리한다. |
| `401` | `INVALID_RUNTIME_TOKEN` | 고정 토큰 불일치. 자동 반복 재시도하지 않고 설정을 확인한다. |
| `404` | `K3S_CREDENTIAL_NOT_FOUND` | 잘못된 ID, 후보 비활성/retired, 또는 저장된 인증정보 없음. Scheduler/Broker 상태를 확인한다. |
| `422` | FastAPI validation error | UUID 또는 query 형식 오류. 요청을 수정한다. |
| `500` | `K3S_CREDENTIAL_DECRYPTION_FAILED` | Broker 저장정보 복호화/무결성 실패. Broker 운영 알림 대상으로 처리한다. |
| `500` | `DATABASE_ERROR` | Broker DB 오류. 제한된 backoff로 재시도할 수 있다. |
| `503` | `RUNTIME_AUTH_NOT_CONFIGURED` | Broker에 `RUNTIME_API_TOKEN`이 없거나 32자 미만이다. |
| `503` | `K3S_CREDENTIAL_STORAGE_NOT_CONFIGURED` | Broker 저장 암호화 설정이 누락됐다. |

인증정보 조회에 성공한 뒤 `server_url` 연결이 시간 초과되면 Broker 인증 문제가
아니다. Runtime에서 대상 주소의 `TCP 6443`까지 방화벽/VPC 경로를 확인한다.

## 보안 요구사항

- `RUNTIME_API_TOKEN`과 `kubeconfig_base64`를 로그에 남기지 않는다.
- 디코딩된 kubeconfig를 공유 디스크, DB, 오류 응답에 저장하지 않는다.
- Runtime API는 HTTPS로만 호출한다.
- 대상 VM의 `6443/tcp`는 Runtime의 고정 출발지 IP나 사설망에서만 허용한다.
- 인증정보 응답을 Scheduler나 일반 사용자에게 전달하지 않는다.
- Runtime 프로세스가 침해되면 고정 토큰과 대상 kubeconfig를 함께 교체한다.

이 API는 Runtime이 k3s에 연결하기 위한 인증정보만 제공한다. Scheduler가
Runtime에 workload 생성·삭제를 지시하는 API는 Runtime 서비스가 별도로 제공해야
한다.
