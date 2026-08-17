# MSG Resource Broker

AWS, GCP, Azure VM inventory를 공통 snapshot으로 관리하는 Resource Broker다.

## Compose 구성

`compose.yaml`은 운영 안전 기본값이다.

- PostgreSQL과 FastAPI는 호스트 포트에 공개하지 않는다.
- React Admin UI는 정적 파일로 빌드해 Nginx가 제공한다.
- Nginx만 기본적으로 `127.0.0.1:8080`에 공개한다.
- DB/Admin secret은 `.env`에 반드시 설정해야 한다.
- GCE에서는 별도 JSON key 없이 attached Service Account의 metadata ADC를 쓴다.
- AWS inventory와 Bootstrap은 같은 GCP Service Account의 Google OIDC ID Token을
  AWS STS Hub/target-account Role로 교환하며 AWS Access Key를 저장하지 않는다.

`compose.dev.yaml`은 로컬 개발 편의만 추가한다.

- PostgreSQL: `127.0.0.1:5432`
- FastAPI: `127.0.0.1:8000`
- Vite: `127.0.0.1:5173`
- Azure Client Secret 전환 경로

## 최초 환경 설정

```powershell
Copy-Item .env.example .env
```

`.env`에서 최소한 다음 값을 실제 값으로 교체한다.

- `POSTGRES_PASSWORD`
- `DATABASE_URL` 안의 URL-encoded DB password
- `ADMIN_ID`
- `ADMIN_PASSWORD`
- `ADMIN_TOKEN_SECRET` (최소 32자 random 값)

`.env`와 Cloud credential은 commit하지 않는다.

## GCP 운영/비공개 테스트 실행

GCE에 전용 Service Account를 연결한 뒤 로컬 ADC override 없이 실행한다.

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail=200 migrate
docker compose logs --tail=200 app
```

기본 Compose만 사용할 때 Admin UI는 GCE의 loopback에만 열리므로 SSH tunnel로
접속한다.

```powershell
gcloud compute ssh BROKER_VM --project=BROKER_PROJECT --zone=BROKER_ZONE -- -L 8080:127.0.0.1:8080
```

브라우저에서 `http://127.0.0.1:8080`을 연다.

운영 GCE에는 호스트 Nginx가 `broker.mjsec.kr`의 일반 HTTPS와
`agents.mjsec.kr`의 Agent mTLS를 종료한다. Docker의 loopback bind는 유지하며
Public IP나 `0.0.0.0`으로 변경하지 않는다. Gateway 설정과 Agent 인증서 절차는
다음 문서에 있다.

- `deploy/nginx/msg-broker.conf`
- `deploy/mtls/README.md`

## Node Agent Bootstrap

Ubuntu AMD64/ARM64 대상 VM의 운영동형 설치는
`deploy/bootstrap/node-agent-bootstrap.sh`를 사용한다. 공통 Bootstrap은 지정
버전 k3s 설치, VM-local CSR/1회용 token enrollment, Node annotation, digest
고정 Agent DaemonSet rollout과 실제 Broker 전달 확인까지 수행한다.

2026-08-10 깨끗한 GCP VM의 수동 Canary에서 최종
`BOOTSTRAP_STATUS=ready`를 확인했다. 이는 공통 package 검증 완료를 뜻하며,
Provider 관리면을 통한 약 100대 검증이 완료됐다는 뜻은 아니다. GCP VM
Manager/OS Config와 AWS Systems Manager adapter는 구현됐으며 실제 Canary 검증은
각 Provider에서 별도로 수행해야 한다.

전체 bundle/API/실행 계약은 `deploy/bootstrap/README.md`를 참고한다. 운영에서는
개인 SSH/SCP로 VM마다 token과 bundle을 전달하지 않는다.

## AWS Provider

AWS는 계정 하나당 Provider Account 하나를 등록하고, 명시한 Region을
`provider_scope_id`로 사용한다. 중앙 federation Hub Role이 서로 독립적으로
소유된 AWS 계정의 고정 이름 Role을 계정별 External ID와 함께 Assume한다.

- 인증: GCP metadata Google OIDC → STS `AssumeRoleWithWebIdentity` → STS
  `AssumeRole`
- 조회: EC2 instances, instance types, attached EBS volumes
- Storage: 연결된 EBS 합계이며 instance store는 제외
- 부분 실패: 성공한 Region만 갱신하고 실패 Region의 마지막 정상 snapshot은 보존
- Bootstrap: 별도 `MsgBrokerBootstrapRole`로 opt-in tag가 붙은 Ubuntu
  AMD64/ARM64 EC2에만 SSM Run Command 실행
- 인증서 발급: IMDSv2 EC2 instance identity RSA 서명을 검증하고 account,
  Region, instance ID를 resource target과 일치시킴

CloudFormation 템플릿과 AWS Console 적용 순서는
`deploy/aws/README.md`를 참고한다. 고객 계정을 Organizations에 초대하거나
AWS Access Key를 전달받지 않는다.

## 로컬 개발 실행

Cloud 인증이 필요 없는 개발:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

로컬 GCP ADC까지 사용하는 개발:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml -f compose.gcp-local.yaml up -d --build
```

상태와 로그:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml ps
docker compose -f compose.yaml -f compose.dev.yaml logs --tail=200 app frontend
```

DB volume을 보존하면서 종료:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml down
```

`down -v`는 PostgreSQL 데이터를 삭제하므로 명시적인 초기화 때만 사용한다.
