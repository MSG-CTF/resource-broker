# Backend VM 목록 및 잔여 리소스 조회 API

별도 Backend가 모든 Provider·계정의 VM 목록을 읽기 전용으로 조회하는 API다. Broker DB의 최신 snapshot을 반환하며 조회 시 Cloud API나 VM에 직접 접속하지 않는다. 이 API의 토큰은 Admin·Scheduler·Runtime 토큰과 별개다.

- **Method:** `GET`
- **Backend Path:** `/v1/inventory/resource-targets`
- **현재 웹 Gateway Path:** `/api/v1/inventory/resource-targets`
- **인증:** `Authorization: Bearer <INVENTORY_API_TOKEN>`
- **성공:** `200 OK`

#### Request

```json
{
  "headers": {
    "Authorization": "Bearer <INVENTORY_API_TOKEN>"
  },
  "query": {},
  "body": null
}
```

`GET`에는 `Content-Type`과 body가 필요 없다. 경로에 VM ID가 없고 `provider`·`account_id` 필터를 생략하면 모든 Provider·계정의 VM을 조회한다.

| Query | 기본값 | 설명 |
| --- | --- | --- |
| `provider` | 전체 | 선택 필터: `AWS`, `GCP`, `AZURE` |
| `account_id` | 전체 | 선택 필터: Broker 계정 UUID |
| `include_retired` | `false` | `true`면 retired VM도 포함 |
| `include_allocations` | `false` | `true`면 현재 페이지의 노드별 예약 원장과 전체 할당 가능 용량 포함 |
| `limit` | `100` | 페이지 크기, 1~500 |
| `offset` | `0` | 건너뛸 개수, 0 이상 |

`total`은 필터 적용 후 전체 개수, `items`는 현재 페이지다. 전체를 받으려면 `offset`을 늘려 `items`가 끝날 때까지 조회한다. 정지·비활성·관측 지연 VM도 목록에 포함될 수 있다.

#### Response

`include_allocations=true`일 때 각 항목에 선택 필드 `resource_allocation`을 추가한다

```json
{
  "resource_allocation": {
    "collected_at": "2026-10-22T00:00:00Z",
    "complete": true,
    "allocatable": {"cpu_millicores": 4000, "memory_mib": 8192},
    "other_reserved": {"cpu_millicores": 0, "memory_mib": 0},
    "allocations": [{
      "reservation_id": "00000000-0000-4000-8000-000000000001",
      "instance_id": "00000000-0000-4000-8000-000000000002",
      "challenge_id": "00000000-0000-4000-8000-000000000003",
      "team_id": "00000000-0000-4000-8000-000000000004",
      "reservation_status": "COMMITTED",
      "expires_at": null,
      "resources": {"cpu_millicores": 500, "memory_mib": 512}
    }]
  }
}
```

- 원장의 만료 전 HELD와 모든 COMMITTED 예약을 같은 SQL 스냅샷에서 조회
- 런타임 관측에 반영된 COMMITTED도 계속 표시하며 실제 반납 또는 만료 후 목록에서 제외
- 각 예약에 기록된 CPU·메모리를 사용하며 현재 문제 릴리스의 설정으로 추정하지 않음
- `allocatable`은 Agent가 보낸 `node_allocatable` 원본이며 실사용량과 실행 요청량 차감 전 총용량
- 기존 `allocatable_capacity`와 `remaining_capacity`는 스케줄링 가용량으로 의미가 다르므로 예약 비율의 분모로 사용하지 않음
- `20261022_0001` migration 후 첫 Agent 관측 전에는 총용량이 null이며 기존 가용량이나 Provider 사양으로 보간하지 않음
- 모든 브로커 예약을 allocations에 포함하므로 other_reserved는 0이며 실제 시스템 사용량을 뜻하지 않음
- 과거 형식의 instance_id가 UUID가 아니면 null로 반환하고 reservation_id와 예약 수치는 보존
- 예약량과 실제 배치 가능한 잔여 자원은 다를 수 있으며 문제·팀 이름은 소비 측 백엔드가 보충
- 기존 호출은 선택 필드를 생략하므로 응답 호환성을 유지

배포 시 migration을 먼저 적용하고 앱을 교체한다
추가한 nullable 열은 이전 앱과 호환되며 롤백 시 데이터베이스를 내리지 않고 이전 앱 이미지를 복구할 수 있다

**200 OK — VM 목록 조회 성공**

```json
{
  "generated_at": "2026-09-15T00:00:01Z",
  "total": 1,
  "limit": 100,
  "offset": 0,
  "items": [
    {
      "resource_target_id": "00000000-0000-4000-8000-000000000001",
      "name": "example-vm",
      "provider": "GCP",
      "account_id": "00000000-0000-4000-8000-000000000002",
      "scope_id": "example-project",
      "instance_id": "example-instance-id",
      "region": "example-region",
      "zone": "example-zone",
      "status": "RUNNING",
      "machine_type": "example-machine-type",
      "architecture": "AMD64",
      "public_ip": "203.0.113.10",
      "provider_capacity": {
        "cpu_millicores": 4000,
        "memory_mib": 8192,
        "storage_mib": 51200
      },
      "runtime_usage": {
        "cpu_millicores": 1200,
        "memory_mib": 3072
      },
      "container_storage_usage": {
        "observed_mib": 1400,
        "observed_container_count": 2,
        "total_container_count": 2,
        "complete": true
      },
      "allocatable_capacity": {
        "cpu_millicores": 1800,
        "memory_mib": 4096,
        "storage_mib": 20000
      },
      "remaining_capacity": {
        "cpu_millicores": 1300,
        "memory_mib": 3072,
        "storage_mib": 17952
      },
      "account_enabled": true,
      "enabled": true,
      "runtime_ready": true,
      "provider_observed_at": "2026-09-15T00:00:00Z",
      "runtime_observed_at": "2026-09-15T00:00:00Z",
      "runtime_last_seen_at": "2026-09-15T00:00:02Z",
      "retired_at": null
    }
  ]
}
```

- CPU는 millicore (`1000` = 1 vCPU), 메모리·스토리지는 MiB다.
- `provider_capacity`는 Cloud가 보고한 VM 전체 사양이다. `storage_mib`는 전체 disk 사양이다.
- `runtime_usage`는 Node Agent가 관측한 **VM 전체 CPU·메모리 실제 사용량**이다. VM 전체 스토리지 실제 사용량은 현재 수집하지 않는다.
- `container_storage_usage.observed_mib`는 **가장 최근 Agent 관측에서 값이 들어온 컨테이너들의 스토리지 사용량 합계**다. VM 전체 디스크 사용량이 아니다. `observed_container_count`는 이번 관측에서 사용량 값이 들어온 컨테이너 수, `total_container_count`는 DB의 current snapshot에 남아 있는 컨테이너 수다. `complete`는 Agent 관측이 있고 DB에 저장된 모든 current container가 이번 관측에서 스토리지 사용량 값을 보낸 경우에만 `true`다. Agent가 VM의 모든 컨테이너를 발견했다는 보장은 아니다. 미관측이면 `observed_mib: null`; Agent 관측 후 저장된 컨테이너가 0개면 `observed_mib: 0`이다.
- `allocatable_capacity`는 Node Agent 관측 기반 **예약 차감 전** 실질 가용량이다. CPU·메모리는 requests 기준과 VM 전체 실사용 기준 중 작은 값이며, `storage_mib`는 ephemeral storage의 node allocatable에서 requests를 뺀 값이다.
- `remaining_capacity`는 `max(allocatable_capacity - deductible_reservations, 0)`이다. 차감 대상은 아직 유효한 `HELD`, 그리고 새 Agent 관측에 아직 반영되지 않은 `COMMITTED` 예약이다. 새 관측으로 반영됐다고 판단할 때는 Agent 시계 오차 30초를 감안한다. `allocatable_capacity`가 `null`인 자원은 `remaining_capacity`도 `null`이다.
- `null`은 미수집이며 `0`이 아니다. `public_ip`도 공인 IP가 없는 VM에서는 `null`이다.
- `generated_at`은 이 응답의 예약 차감 기준 시각이다. Provider·Runtime 관측 시각을 함께 보고 데이터가 오래됐는지 판단해야 한다. 이 API는 정지·비활성·관측 지연 VM도 반환하므로 **배치 가능성 보장이나 예약 확정 응답이 아니다.** 배치는 Scheduler 후보·예약 API를 사용한다.

**401 Unauthorized — 토큰 없음**

```json
{
  "error": {
    "code": "INVENTORY_TOKEN_REQUIRED",
    "message": "A valid Inventory Bearer token is required."
  }
}
```

**401 Unauthorized — 토큰 불일치**

```json
{
  "error": {
    "code": "INVALID_INVENTORY_TOKEN",
    "message": "The Inventory token is invalid."
  }
}
```

**422 Unprocessable Content — Query 값 오류**

`provider`가 지원 값이 아니거나, `account_id`가 UUID가 아니거나, `limit`/`offset` 범위를 벗어나면 FastAPI의 `{"detail": [...]}` 검증 응답을 반환한다.

**500 Internal Server Error — DB 조회 오류**

```json
{
  "error": {
    "code": "DATABASE_ERROR",
    "message": "The inventory could not be loaded."
  }
}
```

**503 Service Unavailable — Inventory 토큰 미설정 또는 32자 미만**

```json
{
  "error": {
    "code": "INVENTORY_AUTH_NOT_CONFIGURED",
    "message": "Inventory authentication is not configured."
  }
}
```

이 조회에는 별도의 `400`, `403`, `404`, `409`, `502` 응답이 정의되어 있지 않다. 성공 응답에는 `code`/`message`/`data` 래퍼가 없고, 인증·DB·설정 오류는 `error.code`/`error.message` 형태다.

## 운영 설정

Broker의 `INVENTORY_API_TOKEN`에 별도 임의 문자열을 32자 이상 설정하고, 호출하는 Backend에도 **같은 값**을 안전한 환경변수나 secret store로 전달한다. Admin JWT, `SCHEDULER_API_TOKEN`, `RUNTIME_API_TOKEN`과 재사용하지 않는다. 토큰 원문은 Git·문서·로그에 저장하지 않는다. 이 토큰은 로그인이나 갱신 없이 쓰는 고정 Bearer 토큰이며, 교체 시 양쪽 값을 갱신한다.

Compose에서 토큰을 설정하지 않아도 기존 API는 실행되지만, 이 Inventory 경로만 `503 INVENTORY_AUTH_NOT_CONFIGURED`를 반환한다. 실제 값을 설정한 뒤 Broker 앱 컨테이너를 재생성해야 한다.

## 실제 환경 확인 절차

1. Broker 배포 디렉터리의 `.env`에 32자 이상 임의 문자열로 `INVENTORY_API_TOKEN`을 설정한다. 호출 Backend에는 같은 값을 별도 secret 설정으로 전달한다.
2. Broker 배포 디렉터리에서 `docker compose up -d --build app`을 실행한다. DB schema 변경은 없으므로 새 migration은 필요 없다.
3. PowerShell 호출 환경에서 아래 명령을 실행한다. 토큰은 화면에 표시되지 않는다.

```powershell
$secureToken = Read-Host 'Inventory token' -AsSecureString
$inventoryToken = [System.Net.NetworkCredential]::new('', $secureToken).Password
Invoke-RestMethod -Method Get -Uri 'https://broker.mjsec.kr/api/v1/inventory/resource-targets?limit=100&offset=0' -Headers @{ Authorization = "Bearer $inventoryToken" }
```

기대 결과는 `200 OK`와 `total`, `items`, 그리고 각 VM의 `provider_capacity`, `runtime_usage`, `container_storage_usage`, `allocatable_capacity`, `remaining_capacity`, `public_ip`다. 토큰을 빼면 `401 INVENTORY_TOKEN_REQUIRED`, 잘못 입력하면 `401 INVALID_INVENTORY_TOKEN`이어야 한다. VM 관측과 예약이 실제로 존재할 때 `remaining_capacity`가 예약 전 값에서 유효한 예약만큼 줄었는지는 [예약 실환경 검증 절차](RESERVATION_VALIDATION.md)와 함께 확인한다.
