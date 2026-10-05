# VM 목록 및 잔여 리소스 조회 API

현재 구현 기준. Broker는 Cloud와 Node Agent가 전달한 최신 값을 DB snapshot으로 조회한다. 조회 요청 시 Cloud나 VM에 실시간으로 접속하지 않는다.

두 용량 필드는 의미가 다르다.

| 목적 | API | 용량 필드 | 의미 |
| --- | --- | --- | --- |
| 관리 화면에서 전체 VM과 사양 확인 | `GET /v1/admin/resource-targets` | `allocatable_capacity` | Node Agent 관측으로 계산한 실질 가용량, Broker 예약 차감 **전** |
| 별도 Backend에서 전체 VM과 잔여량 확인 | `GET /v1/inventory/resource-targets` | `allocatable_capacity`, `remaining_capacity` | 예약 전·후 용량을 함께 반환. [전용 명세](BACKEND_INVENTORY_API.md) |
| Scheduler가 특정 문제를 배치할 VM 선택 | `POST /v1/candidates/query` | `remaining_capacity` | 실질 가용량에서 아직 관측에 반영되지 않은 Broker 예약을 차감한 값 |

별도 Backend에는 읽기 전용 `INVENTORY_API_TOKEN`을 사용한다. Scheduler는 Admin API나 Inventory API를 배치 결정에 사용하지 않고, 조건 검증과 재예약 검사가 있는 후보 API를 사용한다.

API 내부 경로는 `/v1/...`이다. 현재 Broker 웹 Gateway를 통할 때는 `/api/v1/...`로 호출하며, Gateway가 `/api/`를 제거해 Backend에 전달한다. 배포 환경의 호스트명과 내부 서비스 주소는 연동 시 확정한다.

현재 응답은 예시의 `code`/`message`/`data` 공통 래퍼를 사용하지 않는다. 조회 성공은 아래 JSON을 그대로 반환하고, 인증·DB·설정 오류는 `error.code`/`error.message` 형태다. 요청 값 검증 오류는 FastAPI 기본 `detail` 형태다.

## 1. 전체 VM 목록과 예약 전 가용량

- **Method:** `GET`
- **Path:** `/v1/admin/resource-targets`
- **설명:** Broker DB에 저장된 VM 목록, Cloud 전체 사양, Agent 관측 기반 가용량과 상태를 페이지 단위로 반환한다.
- **인증:** Admin Bearer JWT. 이 토큰은 관리자 권한이므로 다른 Backend에 전달할 서비스 토큰으로 사용하지 않는다.

경로에 VM ID가 없다. `provider`와 `account_id`를 생략하면 모든 Provider·계정의 VM을 조회한다. 기본적으로 retired VM만 제외하고, 결과가 많으면 `limit`/`offset`으로 다음 페이지를 조회한다.

#### Request

```json
{
  "headers": {
    "Authorization": "Bearer <ADMIN_JWT>"
  },
  "query": {},
  "body": null
}
```

`GET`에는 요청 본문과 `Content-Type`이 필요 없다. Query는 모두 선택 사항이며, 위 예시는 전체 VM 목록의 첫 페이지를 조회한다.

| Query | 기본값 | 설명 |
| --- | --- | --- |
| `provider` | 전체 | `AWS`, `GCP`, `AZURE` |
| `account_id` | 전체 | Broker가 발급한 Provider 계정 UUID |
| `include_retired` | `false` | `true`면 동기화 결과에서 사라져 retired 처리된 VM도 포함 |
| `limit` | `100` | 1~500 |
| `offset` | `0` | 0 이상. 다음 페이지는 `offset + limit` |

`total`은 필터에 맞는 전체 개수이고 `items`는 현재 페이지다. 기본 조회는 retired VM만 제외하며, 정지·비활성·관측 지연 VM도 목록에 나타날 수 있다.

#### Response

**200 OK — VM 목록 조회 성공**

```json
{
  "total": 1,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "resource_target_id": "00000000-0000-4000-8000-000000000001",
      "provider": "GCP",
      "account_id": "00000000-0000-4000-8000-000000000002",
      "account_display_name": "example-account",
      "external_account_id": "example-owner",
      "scope_id": "example-project",
      "instance_id": "example-instance-id",
      "name": "example-vm",
      "region": "example-region",
      "zone": "example-zone",
      "status": "RUNNING",
      "machine_type": "example-machine-type",
      "architecture": "AMD64",
      "internal_ip": "10.0.0.10",
      "external_ip": null,
      "provider_capacity": {
        "cpu_millicores": 4000,
        "memory_mib": 8192,
        "storage_mib": 51200
      },
      "allocatable_capacity": {
        "cpu_millicores": 1800,
        "memory_mib": 4096,
        "storage_mib": 20000
      },
      "runtime": {
        "type": "KUBERNETES",
        "target_id": "runtime-target-example",
        "ready": true,
        "observed_at": "2026-09-15T00:00:00Z",
        "last_seen_at": "2026-09-15T00:00:02Z",
        "usage": {
          "cpu_millicores": 1200,
          "memory_mib": 3072
        }
      },
      "account_enabled": true,
      "enabled": true,
      "observed_at": "2026-09-15T00:00:00Z",
      "last_seen_at": "2026-09-15T00:00:00Z",
      "retired_at": null
    }
  ]
}
```

- CPU 단위는 millicore (`1000` = 1 vCPU), 메모리·스토리지 단위는 MiB다.
- `provider_capacity`는 Cloud가 보고한 VM 전체 사양이다. `allocatable_capacity`는 Agent가 보고한 node allocatable·workload requests와 VM 전체 실사용량을 반영한 **예약 전** 값이다. `storage_mib`는 가용량 쪽에서는 ephemeral storage에 대응한다.
- `allocatable_capacity`나 `runtime.usage`가 `null`이면 미수집 상태다. `0`으로 해석하면 안 된다.
- 최상위 `observed_at`/`last_seen_at`은 Provider VM snapshot 시각이고, `runtime.observed_at`/`runtime.last_seen_at`은 Agent 관측·수신 시각이다. 오래된 관측값도 이 목록에는 표시될 수 있다.
- `status: "RUNNING"`은 Cloud 전원 상태다. 배치 가능 여부는 `account_enabled`, `enabled`, `runtime.ready`, 관측 신선도와 용량까지 확인해야 한다.
- `scope_id`는 GCP Project ID, Azure Subscription ID 등 Provider별 조회 범위다. `resource_target_id`는 Broker 내부 VM UUID다.

**401 Unauthorized — 토큰 없음**

```json
{
  "error": {
    "code": "ADMIN_TOKEN_REQUIRED",
    "message": "A valid administrator Bearer token is required."
  }
}
```

**401 Unauthorized — 토큰 만료·위조·손상**

```json
{
  "error": {
    "code": "INVALID_ADMIN_TOKEN",
    "message": "The administrator token is invalid or expired."
  }
}
```

**422 Unprocessable Content — Query 값 오류**

`provider`가 지원 값이 아니거나, `account_id`가 UUID가 아니거나, `limit`/`offset` 범위를 벗어나면 FastAPI 기본 검증 오류인 `{"detail": [...]}` 형태를 반환한다. 이 경로는 `400 INVALID_REQUEST`를 반환하지 않는다.

**500 Internal Server Error — DB 조회 오류**

```json
{
  "error": {
    "code": "DATABASE_ERROR",
    "message": "The resource target list could not be loaded."
  }
}
```

**503 Service Unavailable — Admin 인증 설정 오류**

```json
{
  "error": {
    "code": "ADMIN_AUTH_NOT_CONFIGURED",
    "message": "Administrator authentication is not configured."
  }
}
```

이 목록 조회에는 `403`, `404`, `409`, `502` 응답이 별도로 정의되어 있지 않다.

## 2. 특정 문제에 배치 가능한 VM과 예약 차감 후 잔여량

- **Method:** `POST`
- **Path:** `/v1/candidates/query`
- **설명:** 요청한 CPU·메모리·ephemeral storage·아키텍처를 수용할 수 있는 VM 후보만 반환한다. 전체 VM 목록 API가 아니다.
- **인증:** Admin JWT가 아닌 별도 `SCHEDULER_API_TOKEN` Bearer 토큰.

#### Request

```json
{
  "headers": {
    "Content-Type": "application/json",
    "Authorization": "Bearer <SCHEDULER_API_TOKEN>"
  },
  "body": {
    "request_id": "schedule-request-example",
    "requested_at": "2026-09-15T00:00:00Z",
    "team_id": "00000000-0000-4000-8000-000000000003",
    "challenge_id": "00000000-0000-4000-8000-000000000004",
    "instance_id": "example-instance-request",
    "architecture": "AMD64",
    "resource_profile": {
      "cpu_millicores": 500,
      "memory_mib": 1024,
      "ephemeral_storage_mib": 2048
    }
  }
}
```

`request_id`와 `instance_id`는 비어 있지 않은 문자열, `team_id`와 `challenge_id`는 UUID, `requested_at`은 시간대가 있는 시각이다. `architecture`는 `AMD64` 또는 `ARM64`다. `resource_profile`의 세 값은 모두 0보다 큰 정수다.

#### Response

**200 OK — 후보 조회 성공**

```json
{
  "request_id": "schedule-request-example",
  "generated_at": "2026-09-15T00:00:01Z",
  "status": "OK",
  "candidates": [
    {
      "candidate_id": "00000000-0000-4000-8000-000000000001",
      "provider": "GCP",
      "account_id": "00000000-0000-4000-8000-000000000002",
      "region": "example-region",
      "zone": "example-zone",
      "runtime": {
        "type": "KUBERNETES",
        "target_id": "runtime-target-example"
      },
      "architecture": "AMD64",
      "remaining_capacity": {
        "cpu_millicores": 1300,
        "memory_mib": 3072,
        "ephemeral_storage_mib": 17952,
        "fit_count": 2
      },
      "runtime_observed_at": "2026-09-15T00:00:00Z",
      "valid_until": "2026-09-15T00:00:31Z"
    }
  ]
}
```

`candidate_id`는 Admin 목록의 `resource_target_id`와 같다. `remaining_capacity`는 Agent 관측 기반 가용량에서 유효한 `HELD`와 아직 새 관측에 반영되지 않은 `COMMITTED` 예약을 차감한 값이다. `fit_count`는 요청한 `resource_profile`을 남은 용량에 최대 몇 개 더 배치할 수 있는지의 정수값이다. 결과는 최대 50개다.

후보는 계정·VM 활성화, Cloud `RUNNING`, Runtime ready, 아키텍처, 관측 신선도, 용량 조건을 통과한 VM만 포함한다. 조회 결과는 참고용이며 실제 예약 생성 시 Broker가 해당 VM의 조건과 용량을 다시 검사한다.

**200 OK — 조건에 맞는 후보 없음**

```json
{
  "request_id": "schedule-request-example",
  "generated_at": "2026-09-15T00:00:01Z",
  "status": "NO_CANDIDATES",
  "candidates": []
}
```

**401 Unauthorized — 토큰 없음**

```json
{
  "error": {
    "code": "SCHEDULER_TOKEN_REQUIRED",
    "message": "A valid Scheduler Bearer token is required."
  }
}
```

**401 Unauthorized — 토큰 불일치**

```json
{
  "error": {
    "code": "INVALID_SCHEDULER_TOKEN",
    "message": "The Scheduler token is invalid."
  }
}
```

**422 Unprocessable Content — 요청 본문 오류**

필수 필드 누락, UUID·날짜·아키텍처 오류, 0 이하 리소스 값은 FastAPI 기본 `{"detail": [...]}` 검증 응답을 반환한다.

**500 Internal Server Error — DB 조회 오류**

```json
{
  "error": {
    "code": "DATABASE_ERROR",
    "message": "Candidates could not be loaded."
  }
}
```

**503 Service Unavailable — Scheduler 인증 설정 오류**

```json
{
  "error": {
    "code": "SCHEDULER_AUTH_NOT_CONFIGURED",
    "message": "Scheduler authentication is not configured."
  }
}
```

**503 Service Unavailable — Scheduler 용량 설정 오류**

```json
{
  "error": {
    "code": "SCHEDULER_NOT_CONFIGURED",
    "message": "Scheduler capacity settings are not configured."
  }
}
```

이 후보 조회에는 `400`, `403`, `404`, `409`, `502` 응답이 별도로 정의되어 있지 않다. 예약 생성·확정·해제는 별도 [Scheduler API 명세](SCHEDULER_API.md)를 참고한다.
