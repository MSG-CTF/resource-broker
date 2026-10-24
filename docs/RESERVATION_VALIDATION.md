# 예약 관측 인계·만료·VM 실행 상태 수동 검증

사용자가 운영동형 환경에서 직접 수행할 절차다. 자동 테스트 실행 결과가 아니다.
실제 대회 workload가 없는 Canary VM 하나를 사용한다. `<...>`는 환경에 맞게
바꾸고, token이나 인증서 private key를 실행 결과에 포함하지 않는다.

## 배포

이번 수정으로 추가된 DB migration은 없다. 기존 작업본의 migration 적용 상태는
별도로 확인한다. 중앙 Broker에서 현재 사용하는 Compose override를 유지한다.
enrollment override를 사용하는 배포 예:

```bash
docker compose -f compose.yaml -f compose.agent-enrollment.yaml up -d --build app frontend
docker compose -f compose.yaml -f compose.agent-enrollment.yaml ps
docker compose exec -T app alembic current
```

기대 결과: app/frontend/db 정상 상태. 현재 전체 작업본의 migration head는
`20260901_0010`이다. 수집 종료 시각을 보내는 구버전 Agent도 갱신해야 한다.
이미지 빌드 PC에서 저장소 루트를 기준으로 실행한다.

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  --tag <REGISTRY_USER>/msg-broker-node-agent:0.2.1 --push ./node-agent
docker buildx imagetools inspect <REGISTRY_USER>/msg-broker-node-agent:0.2.1
```

multi-platform index digest를 기록한다. Admin 전체 VM → Canary VM 상세 →
Bootstrap UPDATE에 기존 Bootstrap 버전과
`<REGISTRY_USER>/msg-broker-node-agent@sha256:<INDEX_DIGEST>`를 입력한다.
기대 결과: job 성공과 새 Agent observation 수신. VM 후보 등록은 새 관측 확인 후
수행한다. Broker와 VM에서 `timedatectl status`로 시계 동기화를 확인한다.
두 시스템의 시계 오차는 30초 이내여야 한다.

## 요청 준비

아래 명령은 Bash 기준이다. 각 터미널에서 token을 별도로 입력한다.

```bash
BROKER_BASE_URL='https://<BROKER_HOST>/api'
read -rsp 'Scheduler token: ' SCHEDULER_API_TOKEN
printf '\n'
RESERVATION_ID='<검증용 reservation UUID>'
```

[Scheduler API](SCHEDULER_API.md)의 JSON 예시로 `candidate.json`, `hold.json`,
`commit.json`, `release.json`을 준비한다. Canary VM UUID, 해당 architecture,
수용 가능한 resource profile, 검증 전용 team/challenge UUID와 instance ID를
사용한다. 새 예약에는 새 request ID를 쓰고, 재시도에는 `requested_at`까지
원본 요청 그대로 사용한다.

```bash
curl --fail-with-body -sS -H "Authorization: Bearer ${SCHEDULER_API_TOKEN}" \
  -H 'Content-Type: application/json' --data-binary @candidate.json \
  "${BROKER_BASE_URL}/v1/candidates/query"
curl --fail-with-body -sS -H "Authorization: Bearer ${SCHEDULER_API_TOKEN}" \
  -H 'Content-Type: application/json' --data-binary @hold.json \
  "${BROKER_BASE_URL}/v1/reservations"
```

기대 결과: 후보 반환 후 `201 HELD`. 응답의 UUID를 `RESERVATION_ID`와
commit/release 본문의 `reservation_id`에 넣는다. Runtime에 workload 생성을
요청하고 해당 노드에 Pod가 배치돼 요청량이 조회되는 것을 확인한 뒤 commit한다.
Runtime의 단순 요청 접수를 배포 완료로 가정하지 않는다.

## 관측 지연과 차감

중앙 DB에서 읽기 전용 쿼리로 관측·수신·예약 시각을 대조한다.

```bash
docker compose exec db psql -U <POSTGRES_USER> -d <POSTGRES_DB>
```

```sql
SELECT r.reservation_id, r.status, r.committed_at,
       t.runtime_observed_at, t.runtime_last_seen_at,
       t.allocatable_cpu_millicores, t.allocatable_memory_mib,
       r.cpu_millicores, r.memory_mib,
       (r.status = 'HELD' AND r.expires_at > clock_timestamp()) OR
       (r.status = 'COMMITTED' AND
        (t.runtime_observed_at IS NULL OR r.committed_at IS NULL OR
         r.committed_at + interval '30 seconds' >= t.runtime_observed_at))
         AS deductible
FROM reservations r
JOIN resource_targets t USING (resource_target_id)
WHERE r.reservation_id = '<RESERVATION_UUID>';
```

검증 환경의 Agent 디버거 등으로 수집 후 전송을 잠시 보류한다. 그 사이 workload
생성과 아래 commit을 완료하고, 보류한 관측을 정상 mTLS 경로로 전송한다.
실제 운영 VM의 네트워크나 시계를 변경하지 않는다.

```bash
curl --fail-with-body -sS -H "Authorization: Bearer ${SCHEDULER_API_TOKEN}" \
  -H 'Content-Type: application/json' --data-binary @commit.json \
  "${BROKER_BASE_URL}/v1/reservations/${RESERVATION_ID}/commit"
```

기대 결과:

- commit은 `200 COMMITTED`다.
- 수신 시각이 commit 이후여도 수집 시각이 commit 이전이면 `deductible=true`다.
  후보 가용량에서는 해당 예약을 계속 차감한다.
- 이후 관측 시작 시각이 commit + 30초를 넘고 workload requests를 포함하면
  `deductible=false`가 된다. 예약 상태 자체는 계속 `COMMITTED`다.
- 기본 600초보다 오래된 관측이 늦게 도착하거나 재전송돼도 후보로 복귀하지
  않는다. 해당 VM 직접 예약은 `409 CANDIDATE_UNAVAILABLE`다.
- Broker보다 30초를 초과해 미래인 관측은 `409 FUTURE_AGENT_OBSERVATION`이며
  기존 snapshot을 바꾸지 않는다.

전송을 보류할 수단이 없으면 정상 흐름만 확인한 것으로 기록하고 지연 검증
통과로 표시하지 않는다. 시각 비교만으로 실제 workload 반영까지 증명할 수는 없다.

## 조회·commit·만료 경쟁

새 HELD 예약의 만료 시각을 확인한다. 첫 터미널에서 아래 조회를, 두 번째에서
위 commit 명령을 만료 경계에 각각 직접 실행한다. 생성 재시도도 원래
`hold.json`을 그대로 POST해 확인한다.

```bash
curl --fail-with-body -sS -H "Authorization: Bearer ${SCHEDULER_API_TOKEN}" \
  "${BROKER_BASE_URL}/v1/reservations/${RESERVATION_ID}"
```

기대 결과는 다음 둘 중 하나다.

- commit이 성공하면 이후 조회·생성 재시도로 `EXPIRED`가 되지 않는다.
- 만료가 먼저 반영되면 commit은 `409 INVALID_RESERVATION_STATE`다.

성공한 commit의 동일 JSON 재시도는 동일 결과여야 한다. workload를 생성하지
않은 별도의 만료 예약에는 다음 release를 실행한다.

```bash
curl --fail-with-body -sS -H "Authorization: Bearer ${SCHEDULER_API_TOKEN}" \
  -H 'Content-Type: application/json' --data-binary @release.json \
  "${BROKER_BASE_URL}/v1/reservations/${RESERVATION_ID}/release"
```

첫 release는 `200 EXPIRED`와 해제 사유를 기록한다. 동일 요청 재시도는 같은
결과이며 다른 request ID의 release가 기록을 덮어쓰면 안 된다.
workload를 생성한 예약은 Runtime에서 삭제를 완료한 뒤 release한다. commit이
실패했더라도 이미 생성한 workload가 있다면 Runtime에서 정리한다.

HTTP 상태, 비밀값을 제거한 응답, SELECT 결과를 함께 기록한다. 실제 수행 전까지
동시성과 관측 지연의 실환경 검증은 미완료다.

## Cloud 중지 VM의 후보 제외와 복귀

5번 수정은 Broker와 frontend 배포만 필요하며 추가 migration이나 Agent 변경은
없다. 위 Agent `0.2.1` 갱신은 앞서 수행한 관측 시각 수정의 배포 요건이다.
아래 절차에서는 기존 계정과 workload가 없는 Canary를 사용한다.

1. Admin 로그인 → 계정 관리 → Canary 소속 계정 동기화 → 전체 VM에서
   `RUNNING`, Runtime ready, 최신 관측과 충분한 용량을 확인하고 후보 등록한다.
   위 `candidate.json` 조회로 Canary가 반환되는지 확인하고 UUID를 기록한다.
   이 단계에서는 예약을 생성하지 않는다.
2. 해당 Provider Console에서 **Canary만** 중지하고 완료를 기다린다.

   | Provider | Console 경로 | 중지 후 Broker에 저장되는 상태 |
   | --- | --- | --- |
   | GCP | Canary Project → Compute Engine → VM instances → Canary 선택 → Stop | `TERMINATED` |
   | AWS | Canary 계정·Region → EC2 → Instances → Canary 선택 → Instance state → Stop instance | `STOPPED` |
   | Azure | Canary Subscription → Virtual machines → Canary → Overview → Stop | `DEALLOCATED` 또는 `STOPPED` |

   GCP의 `TERMINATED`는 중지 상태이며 삭제를 뜻하지 않는다.
   [GCP 중지·시작](https://docs.cloud.google.com/compute/docs/instances/stop-start-instance),
   [AWS 중지·시작](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/Stop_Start.html),
   [Azure 전원 상태](https://learn.microsoft.com/en-us/azure/virtual-machines/states-billing)를 참고한다.
3. Admin 계정 관리에서 같은 계정을 다시 동기화한다. 전체 VM을 새로 조회해
   상태가 비실행 상태로 반영됐는지 확인한다. 동기화 실패나 이전 `RUNNING`
   snapshot만으로는 이번 검증을 통과한 것으로 기록하지 않는다.
4. 기존 후보 등록은 유지되고 `등록됨 · 현재 사용 불가`와
   `Cloud 동기화에서 실행 중인 VM으로 확인되지 않음`이 표시돼야 한다.
   `candidate.json`을 다시 조회하면 Canary는 제외된다. 다른 적격 VM이 있으면
   응답 전체가 `NO_CANDIDATES`일 필요는 없다.
5. 중지 전 기록한 UUID를 `hold.json`의 `candidate_id`에 넣고, 새로운 request ID와
   아직 예약하지 않은 instance ID로 위 예약 생성 명령을 실행한다.
   기대 결과는 `409 CANDIDATE_UNAVAILABLE`이며 새 예약은 생성되지 않는다.
   기존 예약의 동일 요청 재시도는 기존 멱등성 계약을 유지하므로 이 검증에 쓰지 않는다.

가능하면 기존 Agent 관측의 freshness 기간 안에 3~5를 수행하고, Admin의
`runtime.ready`, `runtime.observed_at`, `runtime.last_seen_at`을 함께 기록한다.
이미 관측이 만료됐다면 비실행 상태와 오래된 관측 중 어느 조건으로 제외됐는지
API 결과만으로 구분할 수 없으므로 그 한계를 기록한다.

후보 등록 API도 직접 확인하려면 Admin 로그인으로 받은 JWT를 숨김 입력한다.
계정은 enabled, Canary는 non-retired여야 아래 상태 오류가 우선 반환된다.

```bash
read -rsp 'Admin token: ' ADMIN_TOKEN
printf '\n'
RESOURCE_TARGET_ID='<중지한 Canary VM의 Broker UUID>'
curl --fail-with-body -sS -X PATCH \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H 'Content-Type: application/json' --data '{"enabled":true}' \
  "${BROKER_BASE_URL}/v1/admin/resource-targets/${RESOURCE_TARGET_ID}"
```

기대 결과: `409 PROVIDER_INSTANCE_NOT_RUNNING`. UI의 후보 등록 해제 또는 같은
API의 `{"enabled":false}` 요청은 중지 상태에서도 `200`으로 성공해야 한다.

같은 Console의 Start/Start instance로 Canary를 시작하고 `RUNNING`을 기다린 뒤
계정을 다시 동기화한다. Runtime ready, 관측 freshness와 용량 조건을 확인한다.
등록을 유지한 VM은 조건 충족 시 후보로 복귀하고, 등록을 해제했다면 다시 후보
등록해야 한다. 위 신규 예약 생성이 `201 HELD`인지 확인하고, workload를 만들지
않았다면 해당 예약을 `SCHEDULER_CANCELLED`로 release해 정리한다.

중지·재시작 자체는 기존 예약이나 등록 설정을 변경하지 않는다. Cloud 상태는
마지막 성공한 sync 기준이므로 sync 전 중지까지 실시간 감지하는 기능은 아니다.
`null`, `UNKNOWN`, 전환 상태, Azure provisioning-only `SUCCEEDED`도 실행 상태로
인정하지 않는다. 실제 관측되지 않은 상태는 실환경 검증 완료로 표시하지 않는다.
