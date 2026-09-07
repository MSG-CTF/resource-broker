# Bootstrap 0.4.2 배포와 실패 작업 확인

이 절차는 사용자가 중앙 Broker와 대상 Canary VM에서 직접 수행한다.
실제 Cloud/VM 검증은 아직 완료되지 않았다. 자동 테스트를 작성하거나 실행하지 않는다.

## 확인된 실패 원인

GCP OS Config 로그의 `[[: not found`에 이어
`MSG_BROKER_K3S_CREDENTIAL_UPLOAD_TOKEN_FILE must be an absolute, readable file`
오류가 발생하면 Bash runner가 `/bin/sh`로 해석되어 토큰 파일 생성이 생략된 것이다.
`NON_COMPLIANT`와 enforce exit code 1은 해당 실행의 실패를 뜻한다.
`Successfully completed ApplyConfigTask`는 OS Config 작업 처리 완료이며
Bootstrap 설치 성공을 뜻하지 않는다.

[GCP Exec interpreter 계약](https://cloud.google.com/python/docs/reference/osconfig/1.17.2/google.cloud.osconfig_v1alpha.types.OSPolicy.Resource.ExecResource.Exec)에
따라 SHELL은 Linux에서 `/bin/sh`를 사용한다. 0.4.1 runner는 Bash 옵션이나
`[[`를 사용하기 전에 POSIX 구문으로 `/bin/bash`에 재진입한다.
0.4.0 이하의 runner 문자열은 저장된 checksum을 보존하기 위해 변경하지 않는다.

같은 bundle의 업로드 토큰 제한도 512자에서 4096자로 수정한다. Broker가 발급하는
job/VM/server URL을 포함한 JWT를 수용하고, 단일 행과 JWT 문자 형식은 검사한다.
JWT 서명, 만료, job/VM 일치 여부는 계속 Broker가 검증한다.

## k3s installer checksum 불일치

`bootstrap.tar.gz: OK` 다음 `install-k3s.sh: FAILED`가 표시되면 외부 k3s
installer 다운로드 내용이 bundle에 고정된 checksum과 달라 실행 전에 중단된 것이다.
이 로그는 Node Agent 이미지 digest 오류를 뜻하지 않는다.
기존 k3s가 있어도 요청 버전이 다르거나 접속 IP의 TLS SAN이 없으면 installer를
다시 실행하므로 UPDATE에서도 이 단계에 진입할 수 있다.

0.4.2는 내용이 바뀔 수 있는 `get.k3s.io` 대신 공식 저장소의 커밋 고정 URL과
확인한 SHA-256을 함께 사용한다. 출처와 검증값은
[installer pin](../deploy/bootstrap/README.md#k3s-installer-pin-갱신)에 기록한다.
checksum 검사는 유지하며, 선택한 k3s 버전과 Agent digest를 임의로 바꾸지 않는다.

## 중앙 Broker 배포

최신 수정 코드와 기존 `.env`, CA가 준비된 저장소 루트에서 Bash로 실행한다.
현재 사용하는 추가 Compose override가 있으면 모든 명령에 유지한다.

```bash
docker compose -f compose.yaml -f compose.agent-enrollment.yaml up -d --build app frontend
docker compose -f compose.yaml -f compose.agent-enrollment.yaml ps -a
docker compose -f compose.yaml -f compose.agent-enrollment.yaml exec -T app \
  ls -l /srv/broker/bootstrap-artifacts/msg-broker-node-agent-bootstrap-0.4.2.tar.gz
```

기대 결과: app/frontend healthy, migrate 정상 종료, 0.4.2 bundle 존재.
이번 수정에는 추가 DB migration과 IAM 변경이 없다. Agent 공개 호스트의 Nginx에는
`POST /v1/agent/bootstrap-jobs/.../k3s-credentials` 전달 경로가 필요하다.
Node Agent는 0.2.1 그대로이므로 이미 push한 multi-platform index digest를 재사용한다.
기존 0.4.0/0.4.1 bundle URL에 새 내용을 덮어쓰지 않는다.

Nginx 설정을 반영한 뒤 빈 JSON으로 업로드 경로만 확인할 수 있다.

```bash
curl -i -sS -X POST \
  -H 'Content-Type: application/json' \
  --data '{}' \
  'https://agents.example.com/v1/agent/bootstrap-jobs/<JOB_ID>/k3s-credentials'
```

기대 결과는 `422 Unprocessable Entity`와 필수 body field 목록이다. 이는 요청이
Nginx의 404에서 끝나지 않고 Broker API까지 도달했다는 뜻이다. 빈 body이므로
인증정보가 저장되지는 않는다.

## 기존 RUNNING 작업

VM 실행이 실패해도 Broker에서 결과 조회 또는 임시 Cloud 리소스 정리가 실패하면
현재 구현은 RUNNING을 유지한다. 재배포로 기존 작업을 성공 처리하거나 수정된
runner로 바꾸지 않는다. 기본 제한시간은 2시간이지만 정리가 계속 실패하면
제한시간이 지나도 RUNNING이 남을 수 있다.

GCP의 `NON_COMPLIANT`는 enforce 실행 전 검사에서도 나타날 수 있다. Broker는
resource config step에 최종 `DESIRED_STATE_CHECK_POST_ENFORCEMENT`가 기록될 때까지
이를 실행 중 상태로 취급하고, 최종 검사 뒤의 `NON_COMPLIANT`만 실패로 확정한다.

보고서에서 결과를 판정할 수 없으면 Broker는 정책 자체의 존재 여부도 조회한다.
정책 GET이 `NotFound`이면 해당 job의 임시 정책/라벨 정리를 재시도하고, 정리가
성공한 뒤 보존된 성공/실패 결과가 있으면 그 결과로 종료한다. 보존된 결과가
없을 때만 `FAILED / GCP_BOOTSTRAP_ASSIGNMENT_MISSING`으로 종료한다.
정책이 삭제된 뒤 보고서가 사라져도 제한시간까지 계속 기다리지 않는다.
실제 실행 결과를 증명할 수 없으므로 성공으로 추정하지 않는다.
정책이 아직 존재하면 기존처럼 보고서를 기다린다. 권한·인증·네트워크 오류를
정책 부재로 취급하지 않으며, 임시 라벨 정리가 실패하면 작업을 활성 상태로 유지한다.
삭제 LRO가 실제 정책을 지운 뒤 빈 응답 변환에서 `TypeError`를 내는 경우에는 정책
부재를 다시 확인해 성공한 삭제로 처리한다.

이 복구 수정은 중앙 Broker의 Python 코드만 변경한다. 최신 코드를 중앙에 반영하고:

```bash
docker compose -f compose.yaml -f compose.agent-enrollment.yaml up -d --build app
```

기존 작업이 다음 처리 차례에서 FAILED로 바뀌고 `completed_at`이 기록되는지
Admin에서 확인한다. 제한시간을 이미 넘겼다면 `BOOTSTRAP_JOB_TIMEOUT`으로
종료될 수 있다. 이는 예상 가능한 실패 작업 종료이며 VM 재설치가 아니다.
이 정책 부재 복구 로직 자체는 Node Agent 이미지, Bootstrap bundle, DB schema를
변경하지 않는다. installer checksum 수정까지 적용하려면 위 app/frontend 재빌드와
새 0.4.2 작업이 필요하다.

중앙 Broker에서 새 진단 로그를 확인한다.

```bash
docker compose -f compose.yaml -f compose.agent-enrollment.yaml logs --since=10m app \
  | grep 'GCP bootstrap job=' | tail -n 20
```

로그는 job ID, stage, 공개 error code, 원인 예외의 클래스 이름만 기록한다.
SDK 원문 오류, runner 본문, JWT, kubeconfig는 출력하지 않는다.

- `stage=read_report`: GCP 실행 결과 조회 단계 오류.
- `stage=check_assignment`: 결과 대기 중 정책 존재 여부 조회 오류.
- `stage=cleanup_missing_assignment`: 정책이 사라진 작업의 임시 리소스 정리 오류.
- `stage=cleanup_pending_result`: 이미 판정한 결과를 보존한 정리 재시도 오류.
- `stage=cleanup_after_report`: 결과 조회 후 임시 정책/라벨 정리 단계 오류.
- `stage=cleanup_after_timeout`: 제한시간 초과 후 정리 단계 오류.
- `stage=apply`: 정책/라벨 적용 단계 오류.

Admin에서 이전 작업이 FAILED 등 종료 상태로 바뀌는지 확인한다.
RUNNING이 계속되면 위 진단 로그를 전달해 해당 단계의 오류를 먼저 해결한다.
DB 상태를 강제로 덮어쓰거나 활성 Cloud 정책을 남긴 채 새 작업을 만들지 않는다.
현재 API는 동일 VM에 활성 작업이 있으면 새 작업을 409로 거절한다.

정책/보고서가 모두 없는 기존 작업의 종료를 확인한 뒤 새 0.4.2 UPDATE를 생성한다.
새 정책이 존재하고 보고서가 아직 없을 때는 RUNNING을 유지해야 하며, 새 작업의
완료 marker, kubeconfig 업로드와 SUCCEEDED까지 아래 절차로 확인한다.

## 새 UPDATE와 대상 VM 확인

기존 작업이 종료되면 Admin → 전체 VM → 대상 Canary 상세 → Bootstrap에서
다음 값을 사용한다.

- action: UPDATE
- Bootstrap version: 0.4.2
- k3s version: 기존에 선택한 버전
- Agent image: `<REGISTRY_USER>/msg-broker-node-agent@sha256:<INDEX_DIGEST>`

대상 VM에서 새 job UUID를 입력하고 확인한다.

```bash
JOB_ID='<새 Bootstrap job UUID>'
sudo test -f "/var/lib/msg-broker-bootstrap/jobs/${JOB_ID}.done" \
  && echo BOOTSTRAP_COMPLETED || echo COMPLETION_NOT_CONFIRMED
sudo k3s kubectl get pods -n msg-broker-system \
  -o custom-columns='NAME:.metadata.name,IMAGE:.spec.containers[*].image'
sudo journalctl -u google-osconfig-agent --since '30 minutes ago' -n 100 --no-pager -o cat
```

기대 결과: 새 digest의 Pod가 Ready, 새 job 완료 marker 존재,
installer 재실행 시 `Verified the pinned k3s installer SHA-256.`,
`The Broker stored the k3s administrator kubeconfig.`와 `BOOTSTRAP_STATUS=ready`,
Admin 작업 SUCCEEDED 및 최신 Agent 관측. 오류 로그를 공유할 때 비밀값은 제외한다.
RUNNING Pod만으로 업로드나 작업 완료를 판정하지 않는다.
