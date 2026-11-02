# 최신 KOTH 구성과 VM 산정 근거

확인일: 2026-10-26. 원격 저장소 `MSG-CTF/2026_MSG_CTF`의 main 커밋
`5770420abcb683d535e807d630a77ef55456797a`를 fetch하고 KOTH 디렉터리를 별도 추출해 확인했다.
원래 작업 폴더의 `feat/web-afterimage` 브랜치와 미커밋 파일은 checkout/merge하지 않았다.
아래는 소스·설정·저장소의 기존 검증 문서에 대한 검토이며 새 부하 시험 결과가 아니다.
최종 적용 범위는 세 프로젝트의 일곱 VM이다. shared는 Card Battle Arena·Jenga·Swanopoly,
heavy는 Dependency Hell 본체·DB와 Runner, pixel_grand는 Pixel Smuggler·Grand Tour Duel이다.

## 문제별 실제 구성

| 문제 | 공식 workload 요청량 | 구성 | VM 시작 사양 |
|---|---|---|---|
| Card Battle Arena | 0.5 CPU / 512 MiB | Python 서비스 1개, SQLite `/data` | 2 CPU / 4 GiB |
| Jenga | 0.3 CPU / 256 MiB | 공식 Dockerfile은 Flask `app.py` 1개, SQLite | 2 CPU / 4 GiB |
| Pixel Smuggler | 1 CPU / 1,024 MiB | Python/이미지 채점 서비스 1개, SQLite; PNG 원본은 보존하지 않음 | 2 CPU / 4 GiB |
| Swanopoly | **6 CPU / 4,096 MiB** | frontend + backend/SQLite + test runner + submit runner | 8 CPU / 8 GiB |
| Grand Tour Duel | **6 CPU / 1,536 MiB** | service 이미지에 HTTP와 6슬롯 격리 runner 내장, SQLite·제출·replay 영속 | 8 CPU / 8 GiB |
| Dependency Hell | info.yaml은 5.5 CPU / 3,072 MiB | 웹/API/점수/coordinator + PostgreSQL + gVisor 일회용 runner | 본체 8/8 + runner 4/4 |

`resource_profile`은 각 문제의 요청량이다. 아래 운영 manifest의 `limits` 합계나 VM 전체 용량과
같은 의미가 아니며, 상한 합계가 평상시 실제 사용량이라는 뜻도 아니다.
Swanopoly와 Grand Tour의 위 요청량에는 runner가 이미 포함되어 있으므로 다시 더하지 않았다.

Jenga 루트에는 다른 다중 서비스 Compose도 있으나 공식 `info.yaml`의 build 경로는
`prob/for_organizer`이고 그 Dockerfile은 `app.py`와 정적 파일을 복사한다.
따라서 이번 산정은 그 공식 실행 경로를 기준으로 했다. 참가자용 다중 서비스 구성을
별도로 운영하려면 그 구성의 수량과 자원을 다시 확인해야 한다.

근거:

- [Card info.yaml](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-card-battle-arena/info.yaml)
- [Jenga 공식 Dockerfile](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-jenga/prob/for_organizer/Dockerfile)
- [Pixel 운영 계약](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-pixel-smuggler/prob/for_organizer/admin.md)
- [Swanopoly runner 구성](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-swanopoly/prob/for_organizer/README-DEPLOYMENT.md)
- [Grand Tour 운영 구성](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-grand-tour-duel/prob/for_organizer/admin.md)

## Dependency Hell을 나누는 이유

운영 진입점은 `deploy/k8s/hostile-production`이다. 로컬 Compose와 이름이
`compose.production.yml`인 파일도 실제 대회용 참가자 코드 실행 경계는 아니라고 명시한다.
단일 호스트 리허설용 Compose가 실행된다는 사실로 운영 배포 지원을 판단하지 않았다.

원본 Kubernetes manifest의 상시 복제본과 container limits 합계:

| 역할 | 복제본 / 동시 수 | CPU 상한 합계 | RAM 상한 합계 |
|---|---:|---:|---:|
| frontend | 2 | 1 | 256 MiB |
| public backend | 2 | 2 | 1,024 MiB |
| score-service | 2 | 1 | 768 MiB |
| coordinator | 1 | 1 | 768 MiB |
| disposable runners | 최대 동시 평가 2 | 2 | 1,280 MiB |
| **합계** | | **7** | **4,096 MiB** |

이 합계에는 PostgreSQL, OS/k3s, 배포 시 일시적인 migration/init 작업과 rollout 여유가 없다.
coordinator ConfigMap의 `MAX_ACTIVE_EVALUATIONS=2`를 기준으로 runner 두 개를 합산했다.
runner ResourceQuota의 16개는 namespace 상한이며 정상 동시 평가 수 2와 구분한다.
두 노드 모두 측정 전 시작 사양이며, PostgreSQL의 실제 부하나 모든 동시 상한을 보장하지 않는다.

coordinator는 노드 라벨 `dependency-hell.io/node-pool=trusted-services`를,
runner admission은 같은 라벨 키의 `untrusted-judge` 값과 별도 taint를 요구한다.
노드 하나가 같은 키에 두 값을 가질 수 없으므로 한 VM의 CPU/RAM 증설로 이 배치 조건을 해결할 수 없다.
사용자가 승인한 두 VM을 같은 k3s cluster의 server/agent로 연결하도록 사설 방화벽 경로를 준비했다.

모든 문제 구성요소를 VM에 둔다는 요청에 따라 PostgreSQL은 본체 VM에 자체 운영할 계획이다.
현재 운영 문서는 managed PostgreSQL과 정확한 외부 DB egress 주소를 전제한다.
따라서 자체 PostgreSQL 설치, TLS CA/호스트 검증, DB/owner/runtime 역할, pgcrypto,
백업·복구, egress 정책과 RWX 저장소 연결을 실제 환경에 맞추는 작업은 남아 있다.
Terraform이 VM을 만들었다고 이 계약이 구현되지는 않는다.

제출물 디스크는 원본 운영 PVC의 100 GiB를 유지했다. `service.py`의 `prune_artifacts()`는
PENDING과 검증된 팀별 최선 PASS의 파일 참조를 보존하고 나머지 참조/고아 파일을 정리한다.
Grand Tour처럼 모든 경기 리플레이를 그대로 누적하는 저장 모델과는 다르다.
이 정리 경로와 디스크 여유 경보가 실제 운영에서 동작하는지 확인해야 한다.

근거:

- [운영 배포 문서](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/deploy/README.md)
- [Frontend 복제본과 자원](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/deploy/k8s/base/frontend.yaml)
- [Public backend](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/deploy/k8s/base/backend.yaml)
- [Score service](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/deploy/k8s/base/backend-internal.yaml)
- [Coordinator](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/judge-coordinator/manifests/coordinator.yaml)
- [동시 평가 설정](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/judge-coordinator/manifests/config.yaml)
- [제출물 정리 경로](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/service/service.py#L2233)
- [Runner 배치 정책](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-dependency-hell/prob/for_organizer/judge-coordinator/manifests/runner-admission-policy.yaml)

## 2주 테스트 후 초기화, 12시간 실제 경기 기준

현재 저장소의 기존 baseline 문서에는 75팀, 공식 740경기, tick 하나의
DB·제출·리플레이 약 631.8 MiB가 기록되어 있다. 이는 우리가 새로 실행한 측정이 아니다.
실제 참가 팀 수·봇·연습 빈도·현재 버전에 따라 저장량은 달라진다.

```text
실제 경기 공식 구간 수 = 12시간 × 시간당 4구간 = 48
공식 구간 단순 외삽 = 631.8 MiB × 48 ÷ 1,024 = 29.615625 GiB
```

사용자는 2주를 테스트 기간으로 설명했고, 테스트 후 초기화하며 대회는 12시간이라고 정정했다.
2주 내내 경기를 누적한다는 기존 가정과 추가 2 TiB 디스크를 삭제했다.
CPU/RAM과 원본 `info.yaml` 임시 디스크 요청량은 유지한다. VM 부팅 디스크는 기본 30~50 GiB,
Dependency 제출물 PVC는 원본 요청 100 GiB만 반영한다.
Grand는 별도 디스크 없이 기존 50 GiB 부팅 디스크의 host 영속 볼륨을 사용하도록 인프라를 준비한다.
연습·추가 제출·백업·OS·이미지는 위 29.6 GiB 외삽에 포함되지 않는다.
테스트에서 실제 사용량을 확인해야 하며, 50 GiB가 모든 부하를 수용한다는 검증 결과는 아니다.
테스트 데이터 초기화는 운영 전 별도 작업이며 이 Terraform에 파괴적인 초기화 동작을 추가하지 않았다.

또한 최신 원격 문서에는 75팀 중첩 시험이 두 번째 tick에서 컨테이너 메모리 한도
1,536 MiB에 도달해 자원 기준을 통과하지 못했다고 기록한다. OOM은 0이며 파일 캐시 비중이 컸다.
지속 시험은 미완료로 표시한다. 원시 측정 산출물은 이번 검토에서 독립 검증하지 않았다.
VM 8 GiB와 컨테이너 1.5 GiB 한도는 별개이므로, host RAM만 늘려 이 검수가 해결됐다고 말할 수 없다.
운영 전 출제 서비스의 현재 memory limit·구현·장기 부하를 실제 환경에서 확인해야 한다.

근거:

- [기존 75팀 단일 tick 측정](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-grand-tour-duel/prob/for_organizer/VERIFICATION.md)
- [운영 안정화 미완료 기록](https://github.com/MSG-CTF/2026_MSG_CTF/blob/5770420abcb683d535e807d630a77ef55456797a/koth-grand-tour-duel/prob/for_organizer/OPERATIONAL_READINESS_VERIFICATION.md)

세 프로젝트의 quota/plan과 VM 생성은 확인했다. 다음 작업은 Dependency Hell 두 노드의
k3s/gVisor 및 자체 PostgreSQL/PV 설치, Grand Tour memory limit/지속 부하 검증과 host 영속 경로 연결이다.
