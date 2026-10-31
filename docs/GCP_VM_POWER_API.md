# GCP 문제 VM 전원 관리

Broker Admin 로그인 후 `전체 VM`에서 `KOTH` 또는 `일반 문제`로 필터링하고,
GCP 문제 VM을 선택해 `선택 VM 시작` 또는 `선택 VM 중지`를 누른다.
선택한 VM 중 현재 상태가 각각 `TERMINATED` 또는 `RUNNING`인 VM만 요청한다.
각 VM은 `진행 중` → `완료` 또는 `오류`로 표시된다. 중지해도 디스크와 고정 IP
비용은 계속 발생한다. 시작 후 Scheduler 후보가 되려면 Node Agent가 다시
`ready` 관측을 보내야 한다.

서버는 Admin JWT, 등록된 활성 GCP 계정과 Project, Terraform 문제 Project 목록,
실제 GCP VM 숫자 ID와 `msg-problem`/`msg-koth` 네트워크 태그를 확인한다.
Azure Windows와 중앙 서비스 VM은 이 API의 대상이 아니다. Broker가 연결한
기존 Service Account의 ADC를 사용한다. 이 계정에는 대상 Project에서 VM 조회,
시작, 중지 및 zone operation 조회 권한이 필요하다. Cloud IAM 변경은 사용자가
대상 Project별로 수행한다.

## API

공통 헤더: `Authorization: Bearer <ADMIN_TOKEN>`, `Content-Type: application/json`.

| Method | Path | Body | 성공 |
| --- | --- | --- | --- |
| POST | `/v1/admin/resource-targets/{resource_target_id}/power` | `{"action":"STOP"}` 또는 `{"action":"START"}` | `202`, `{"status":"RUNNING","vm_status":"STOPPING","operation_name":"..."}`. 이미 원하는 상태면 `status=DONE`, `operation_name=null` |
| GET | `/v1/admin/resource-targets/{resource_target_id}/power-operations/{operation_name}?action=STOP` | 없음 | `200`, `status=RUNNING` 또는 `DONE`, `vm_status`, `operation_name` |

오류는 `{"error":{"code":"...","message":"..."}}` 형식이다.
`401`은 Admin 인증 실패, `404`는 Broker VM ID 없음, `409`는 대상 불일치·전원
변경 중·GCP 작업 실패, `422`는 요청 형식 오류, `500`은 DB 실패, `502`는 GCP
호출 실패, `503`은 GCP SDK 부재다. 원본 Cloud 오류와 credential은 응답하지 않는다.
프론트는 3초마다 GET을 호출하며 5분 안에 완료를 확인하지 못하면 오류를 표시한다.
이 경우 새로고침과 Provider sync로 실제 상태를 확인한다.

Terraform의 `desired_status`는 생성 시 초기 상태만 정한다. 기존 VM의 전원 상태는
`ignore_changes`로 제외돼 다음 `terraform apply`가 대시보드 동작을 되돌리지 않는다.
