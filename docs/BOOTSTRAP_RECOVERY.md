# Bootstrap 0.4.1 배포와 실패 작업 확인

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

## 중앙 Broker 배포

최신 수정 코드와 기존 `.env`, CA가 준비된 저장소 루트에서 Bash로 실행한다.
현재 사용하는 추가 Compose override가 있으면 모든 명령에 유지한다.

```bash
docker compose -f compose.yaml -f compose.agent-enrollment.yaml up -d --build app frontend
docker compose -f compose.yaml -f compose.agent-enrollment.yaml ps -a
docker compose -f compose.yaml -f compose.agent-enrollment.yaml exec -T app \
  ls -l /srv/broker/bootstrap-artifacts/msg-broker-node-agent-bootstrap-0.4.1.tar.gz
```

기대 결과: app/frontend healthy, migrate 정상 종료, 0.4.1 bundle 존재.
이번 수정에는 추가 DB migration, IAM 변경, Nginx 경로 변경이 없다.
Node Agent는 0.2.1 그대로이므로 이미 push한 multi-platform index digest를 재사용한다.
기존 0.4.0 bundle URL에 새 내용을 덮어쓰지 않는다.

## 기존 RUNNING 작업

VM 실행이 실패해도 Broker에서 결과 조회 또는 임시 Cloud 리소스 정리가 실패하면
현재 구현은 RUNNING을 유지한다. 재배포로 기존 작업을 성공 처리하거나 수정된
runner로 바꾸지 않는다. 기본 제한시간은 2시간이지만 정리가 계속 실패하면
제한시간이 지나도 RUNNING이 남을 수 있다.

중앙 Broker에서 새 진단 로그를 확인한다.

```bash
docker compose -f compose.yaml -f compose.agent-enrollment.yaml logs --since=10m app \
  | grep 'GCP bootstrap job=' | tail -n 20
```

로그는 job ID, stage, 공개 error code, 원인 예외의 클래스 이름만 기록한다.
SDK 원문 오류, runner 본문, JWT, kubeconfig는 출력하지 않는다.

- `stage=read_report`: GCP 실행 결과 조회 단계 오류.
- `stage=cleanup_after_report`: 결과 조회 후 임시 정책/라벨 정리 단계 오류.
- `stage=cleanup_after_timeout`: 제한시간 초과 후 정리 단계 오류.
- `stage=apply`: 정책/라벨 적용 단계 오류.

Admin에서 이전 작업이 FAILED 등 종료 상태로 바뀌는지 확인한다.
RUNNING이 계속되면 위 진단 로그를 전달해 해당 단계의 오류를 먼저 해결한다.
DB 상태를 강제로 덮어쓰거나 활성 Cloud 정책을 남긴 채 새 작업을 만들지 않는다.
현재 API는 동일 VM에 활성 작업이 있으면 새 작업을 409로 거절한다.

## 새 UPDATE와 대상 VM 확인

기존 작업이 종료되면 Admin → 전체 VM → 대상 Canary 상세 → Bootstrap에서
다음 값을 사용한다.

- action: UPDATE
- Bootstrap version: 0.4.1
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
`The Broker stored the k3s administrator kubeconfig.`와 `BOOTSTRAP_STATUS=ready`,
Admin 작업 SUCCEEDED 및 최신 Agent 관측. 오류 로그를 공유할 때 비밀값은 제외한다.
RUNNING Pod만으로 업로드나 작업 완료를 판정하지 않는다.
