# MSG Resource Broker

AWS, GCP, Azure VM inventory를 공통 snapshot으로 관리하는 Resource Broker다.

## Compose 구성

`compose.yaml`은 운영 안전 기본값이다.

- PostgreSQL과 FastAPI는 호스트 포트에 공개하지 않는다.
- React Admin UI는 정적 파일로 빌드해 Nginx가 제공한다.
- Nginx만 기본적으로 `127.0.0.1:8080`에 공개한다.
- DB/Admin secret은 `.env`에 반드시 설정해야 한다.
- GCE에서는 별도 JSON key 없이 attached Service Account의 metadata ADC를 쓴다.

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

기본 Admin UI는 GCE의 loopback에만 열리므로 SSH tunnel로 접속한다.

```powershell
gcloud compute ssh BROKER_VM --project=BROKER_PROJECT --zone=BROKER_ZONE -- -L 8080:127.0.0.1:8080
```

브라우저에서 `http://127.0.0.1:8080`을 연다. 외부 HTTPS/mTLS ingress는 Node
Agent 수집 API를 구현할 때 추가한다.

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
