# KOTH VM 7대 / GCP 프로젝트 3개

2026-10-26 사용자 결정: 프로젝트 세 개, `us-central1-a`, 참가자가 VM별 공인 IP로 직접 접속.
기존 `terraform/` 루트와 분리된 독립 Terraform 구성이다.
최신 문제 검토 후 사용자가 Nonce를 Pixel Smuggler로 교체하고 Dependency Hell을 VM 두 대로
분리하기로 결정했다. **2주는 테스트 기간이며, 테스트 후 초기화하고 실제 경기는 12시간**이다.
VM의 CPU/RAM은 앞서 정한 값을 유지하고 문제의 임시 디스크 프로필은 원본을 따르며, 2주 리플레이 누적을 가정한
추가 2 TiB 디스크는 제외했다.
검토한 원격 main은 `5770420abcb683d535e807d630a77ef55456797a`이며,
구성 근거와 한계는 [SOURCE_REVIEW.md](SOURCE_REVIEW.md)에 정리했다.
`shared`의 Card Battle Arena·Jenga·Swanopoly 세 VM은 유지한다.
`heavy`에는 Dependency Hell 본체·DB와 Runner를, `pixel_grand`에는 Pixel Smuggler와
Grand Tour Duel을 배치한다. 문제 여섯 개, VM 일곱 대다.

## 생성 범위와 사양

다음 표는 세 프로젝트를 모두 활성화했을 때의 전체 설계다.
실제 생성 범위는 `enabled_project_groups`와 `enabled_shared_challenges`로 선택한다.
현재 세 VM의 CPU/RAM은 각각 2 vCPU/4 GiB, 2 vCPU/4 GiB, 8 vCPU/8 GiB로 유지한다.

| 프로젝트 키 | 문제 | GCP machine type | vCPU / RAM | pd-balanced 디스크 |
|---|---|---|---|---|
| shared | card-battle-arena | e2-custom-2-4096 | 2 / 4 GiB | 부팅 30 GiB |
| shared | jenga | e2-custom-2-4096 | 2 / 4 GiB | 부팅 30 GiB |
| pixel_grand | pixel-smuggler | e2-custom-2-4096 | 2 / 4 GiB | 부팅 30 GiB |
| shared | swanopoly | e2-highcpu-8 | 8 / 8 GiB | 부팅 50 GiB |
| heavy | dependency-hell 본체·DB | e2-highcpu-8 | 8 / 8 GiB | 부팅 50 GiB + 제출물 100 GiB |
| heavy | dependency-hell-runner | e2-highcpu-4 | 4 / 4 GiB | 부팅 30 GiB |
| pixel_grand | grand-tour-duel | e2-highcpu-8 | 8 / 8 GiB | 부팅 50 GiB |

위 부팅 디스크는 Ubuntu·컨테이너 이미지·로그와 영속 볼륨을 담을 VM 저장소다.
문제 `info.yaml`의 임시 디스크 프로필은 Card 1 GiB, Dependency 4 GiB, Grand 2 GiB,
Jenga 0.5 GiB, Pixel 1 GiB, Swanopoly 2 GiB를 유지한다. 이 값으로 부팅 디스크를 만드는 것은 아니다.
Dependency의 별도 100 GiB는 원본 운영 PVC의 요청값이다.

활성화한 프로젝트에 새 custom VPC와 subnet을 하나씩 만들고, 각 VM에 고정 외부 IPv4를 붙인다.
OS는 일반 Ubuntu 24.04 LTS AMD64이고 Spot이 아닌 STANDARD VM이다.
VM 이름은 `msg-문제이름-1`이다. 예를 들어 `msg-jenga-1`, `msg-dependency-hell-runner-1`이다.
Dependency Hell의 본체에는 웹/API/점수 서비스/coordinator/PostgreSQL을,
별도 runner VM에는 gVisor 채점 작업 두 개와 k3s agent를 배치하는 인프라 계획이다.
본체를 12/16으로 키우는 대신 운영 manifest의 trusted/untrusted 노드 분리를 반영했다.
자체 PostgreSQL의 TLS·역할·백업, 클러스터·PV·gVisor 설치는 별도 배포 작업이다.
실제 수용 가능한 제출량은 측정하지 않았으며 사양은 부하 시험 전 시작값이다.

이 구성은 VM, VPC, 방화벽, 디스크와 Compute API를 준비한다.
shared에는 `osconfig.tf`로 OS Config API와 프로젝트 메타데이터 `enable-osconfig=TRUE`도 적용한다.
VM의 연결 서비스 계정과 중앙 Broker의 OS Policy 권한은 별도 선행 조건이므로,
이 활성화만으로 Node Agent가 설치되지는 않는다.
k3s, Docker, 문제 이미지, DB, TLS, 플랫폼 인증/점수 연동, Broker 등록/Agent는 별도 설치 단계다.
`terraform apply` 성공은 문제 서비스 실행 완료를 의미하지 않는다.

## 입력과 선행 조건

필수 입력은 서로 다른 프로젝트 ID 세 개다. 일부 그룹만 선택해도 입력 객체에는 세 ID를 유지한다.
배포할 프로젝트는 미리 생성하고 결제를 연결해야 한다.
Terraform은 프로젝트와 Billing account를 만들지 않는다. `heavy`와 `pixel_grand`에서는
기존 중앙 Broker 서비스 계정에 조회 전용 `roles/compute.viewer`를 부여한다.
VM 시작·중지 권한은 이 구성에 포함되지 않는다.

실행 계정에는 활성화한 프로젝트의 API 활성화, VPC/subnet/firewall, IP, VM/disk 생성 권한이 필요하다.
Broker IAM grant를 적용하려면 `heavy`와 `pixel_grand` 프로젝트의 IAM 정책 조회·변경 권한도 필요하다.
제한된 역할로 운영하는 경우 기존 관리자가 실행 계정의 권한을 확인한다.
대표적으로 `roles/serviceusage.serviceUsageAdmin`, `roles/compute.instanceAdmin.v1`,
`roles/compute.networkAdmin`, `roles/compute.securityAdmin`이 해당 작업에 쓰인다.
VM에는 service account를 연결하지 않는다.
SSH는 사용자 요청에 따라 OS Login을 끄고 VM별 metadata의 `sla` 공개키를 사용한다.
동봉한 `sla_to_control.pub`는 공개키이며 개인키는 `~/.ssh/sla_to_control`에 그대로 보관한다.
개인키를 Terraform state, 이 폴더 또는 VM에 복사하지 않는다.

us-central1의 **기존 사용량을 제외한 여유 quota**를 확인한다.

| 선택 범위 | E2 vCPU | 사용 중 외부 IPv4 | 디스크 총량 |
|---|---:|---:|---:|
| shared | 12 | 3 | 110 GiB |
| heavy | 12 | 2 | 180 GiB |
| pixel_grand | 10 | 2 | 80 GiB |

활성화한 프로젝트의 전체 CPU quota도 충족해야 한다. quota와 zone의 실제 자원 가용성은 별개다.
2026-10-26 생성 후 API 재조회에서 shared의 `CPUs (All Regions)` 한도는 **12**, 사용량도 **12**였다.
현재 shared 세 VM이 이 한도를 사용 중이다.
추가 생성 전 heavy와 pixel_grand는 각각 전역 CPU 한도 16, 사용량 0이었고,
리전 SSD 한도도 각각 300 GiB여서 필요한 180/80 GiB가 들어간다.
추가 배포 시 GCP 콘솔 → IAM 및 관리자 → 할당량 및 시스템 한도에서 Compute Engine의
`CPUs (All Regions)`를 확인한다.
전체 일곱 VM의 생성, 고정 IP 연결과 SLA 키 SSH 접속을 확인했다.
생성 후 전역 CPU 사용량/한도는 shared **12/12**, heavy **12/16**, pixel_grand **10/16**이다.
Free Trial Billing account는 quota 변경을 요청할 수 없으므로, 필요한 quota가 없으면
계정 업그레이드나 배치 변경을 먼저 결정해야 한다. 이 코드는 quota를 변경하지 않는다.

```bash
gcloud compute regions describe us-central1 --project=SHARED_PROJECT_ID --format="json(quotas)"
gcloud compute regions describe us-central1 --project=HEAVY_PROJECT_ID --format="json(quotas)"
```

## 실행

아래 명령은 **이 README가 있는 koth 디렉터리**에서 실행한다.
기존 상위 `terraform/` 디렉터리에서 실행하면 다른 구성을 적용하게 된다.

로컬 PC에서는 먼저 사용자 ADC를 설정한다. 자격 증명 JSON을 이 폴더에 복사하지 않는다.

```powershell
gcloud auth application-default login
Set-Location C:\Users\USER\Desktop\MSG-broker\terraform\koth
Copy-Item terraform.tfvars.example terraform.tfvars
```

Cloud Shell에서는 업로드한 구성 디렉터리로 이동해서 다음과 같이 입력 파일을 만든다.

```bash
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars`에서 프로젝트 ID를 입력하고 배포 그룹을 선택한다.
`enabled_project_groups` 기본값은 `["shared", "heavy", "pixel_grand"]`다.
`enabled_shared_challenges`는 shared의 세 문제를 선택하며 Pixel은 pixel_grand에 속한다.

```hcl
project_ids = {
  shared      = "YOUR_SHARED_PROJECT_ID"
  heavy       = "YOUR_DEPENDENCY_PROJECT_ID"
  pixel_grand = "YOUR_PIXEL_GRAND_PROJECT_ID"
}

enabled_project_groups = ["shared", "heavy", "pixel_grand"]

enabled_shared_challenges = [
  "card-battle-arena",
  "jenga",
  "swanopoly",
]
```

```bash
terraform init
terraform fmt -check
terraform validate
terraform plan -out=koth.tfplan
terraform apply koth.tfplan
terraform output vms
```

빈 state에서 전체 적용하면 **VM 7대, IP 7개, 디스크 8개, VPC/subnet 각 3개,
방화벽 12개, API 4개, 프로젝트 메타데이터 1개로 총 45개 생성**이 예상된다.
기존 shared 세 VM이 있는 이번 확장 계획은 **28개 생성, 기존 15개 변경·삭제 없음**이다.
실제 저장 plan의 개수와 대상을 확인하고, 기존 리소스 삭제나 교체가 보이면 적용을 중단한다.
이미 적용한 그룹이나 문제를 선택에서 빼면 해당 리소스 삭제 계획이 생길 수 있으므로,
기존 VM의 일시 중지는 Broker 대시보드의 GCP 문제 VM 전원 관리에서 수행한다.
이미 같은 이름의 리소스가 있으면 자동 인수하지 않고 충돌한다.
재실행할 때 같은 Terraform state와 입력 파일을 보존한다.
기존 Nonce 구성을 이미 적용했다면 Pixel 교체는 이름/주소 변경을 포함하므로 기존 VM 삭제와
신규 생성 계획이 나올 수 있다. 삭제 보호를 우회하지 말고 기존 데이터와 state부터 확인한다.

Terraform state에는 리소스 ID와 운영 정보가 있으므로 공개 저장소에 올리지 않는다.
이 저장소는 원래 `/terraform` 전체를 Git에서 제외하고 있으며 그 정책은 유지했다.

## 접속과 데이터

- 참가자 포트: runner를 제외한 여섯 VM의 TCP 8080을 인터넷에 허용한다.
- 헬스체크: shared의 Card Battle, Swanopoly TCP 9090을 공개한다.
  Dependency Hell 본체와 Grand Tour에도 TCP 9090을 공개한다.
  Jenga와 Pixel은 8080의 `/healthz`를 사용한다.
- 관리자/SLA SSH: 사용자 요청에 따라 TCP 22를 인터넷 전체에서 허용한다. 인증은 SLA 키를 사용한다.
  프로젝트 공통 SSH 키는 차단하고 각 VM에 등록한 공개키를 사용한다.
- heavy를 활성화하면 Dependency Hell 두 노드 사이에서만 TCP 6443/10250과 UDP 8472를 사설망으로 허용한다.
  DB, runner 내부 포트와 k3s API는 인터넷에 공개하지 않는다.

`terraform output vms`의 `ssh_command`로 관리자 접속한다. 예:

```bash
ssh -i ~/.ssh/sla_to_control sla@VM_PUBLIC_IP
```

원격 Runtime이 직접 k3s API를 호출하는 경로는 이번 설정에 포함하지 않는다.
호스트 키를 Cloud API로 확인할 수 있도록 최초 부팅 전 guest attributes를 활성화한다.
문제 설치 후 `participant_url`이 응답한다. 출력의 HTTP URL에는 TLS가 없으므로,
실제 참가자 팀 토큰을 받기 전에 HTTPS 경로와 인증서 구성을 완료해야 한다.

부팅 디스크는 명시적인 Persistent Disk 리소스로 관리하며 VM 삭제 시 자동 삭제하지 않는다.
부팅/제출물 디스크에 `prevent_destroy`, VM에 deletion protection이 적용된다.
각 문제의 DB, 점수, 제출물 경로를 영속 저장소에 연결하고 별도 백업해야 한다.
삭제 방지는 백업이나 고가용성을 제공하지 않는다.

Dependency Hell의 추가 디스크는 연결된 **미포맷 block disk**다.
`/dev/disk/by-id/google-dependency-artifacts`를 파일시스템으로 준비하고 제출물 경로에
연결하는 작업은 문제 설치 시 수행한다. 이 디스크 자체는 운영 명세의 RWX PVC를 만들지 않는다.
본체에 k3s server, runner VM에 같은 클러스터의 k3s agent를 설치한다.
본체 노드에는 `dependency-hell.io/node-pool=trusted-services`, runner 노드에는
`dependency-hell.io/node-pool=untrusted-judge` 라벨과 운영 manifest의 taint를 설정한다.
Terraform의 GCE network tag는 Kubernetes node label/taint를 설정하지 않는다.
운영 배포는 자체 PostgreSQL의 TLS/역할/백업, RWX PVC, Secret, gVisor와 admission/NetworkPolicy
계약을 추가로 충족해야 한다. 원본 운영 overlay의 managed-DB 경로도 자체 DB 주소로 조정해야 한다.
E2는 nested virtualization을 지원하지 않으므로 E2에서 구성할 때는 gVisor 경로를 검토한다.
KVM 기반 Kata가 필요하면 지원 machine series와 배포 구조를 다시 선택해야 한다.

Grand Tour 최신 `info.yaml`은 내장 runner를 포함해 **6 vCPU / 1.5 GiB**다.
문제 설치 시 `/var/lib/koth`의 DB·제출·리플레이를 host의 영속 볼륨에 연결한다.
원본 문서의 과거 75팀 단일 tick 측정 631.8 MiB를 실제 경기 12시간의 48구간에
단순 외삽하면 약 29.6 GiB다. 이는 실제 12시간 측정치가 아니며 연습·백업은 별도다.
사용자가 요청한 원본 문제 프로필과 기본 VM 디스크로 시작하고, 테스트에서 실제 저장량을 확인한다.
테스트 데이터는 대회 전에 초기화할 계획이며, 이 Terraform이 초기화를 자동 실행하지 않는다.
운영 문서에는 1,536 MiB 컨테이너 한도에서 중첩 부하 검수가 미통과라고 기록되어 있다.
VM RAM을 확보해도 컨테이너 한도는 자동 변경되지 않는다. 운영 전 한도·구현과 지속 부하 검증이 남는다.

## 2주 예상 비용

조회일: 2026-10-26. us-central1, 14일 × 24시간 = **336시간**, USD 일반 종량제 기준.
무료 크레딧, 세금, 약정, Spot 할인은 적용하지 않았다.
기존 shared 세 VM은 **$115.62**, Dependency 두 VM은 **$111.38**,
Pixel·Grand 두 VM은 **$93.05**다. 일곱 VM 전체는 반올림 전 합계로 **$320.05**다.

확인한 단가:

- E2 custom vCPU: $0.02290217 / vCPU-hour
- E2 custom RAM: $0.003069707 / GiB-hour
- e2-highcpu-8: $0.19788096 / VM-hour
- e2-highcpu-4: $0.09894048 / VM-hour
- pd-balanced: $0.00013699 / GiB-hour
- 사용 중 외부 IPv4: $0.005 / IP-hour

| 프로젝트 | 문제 | VM | 디스크 | IPv4 | 2주 합계 |
|---|---|---:|---:|---:|---:|
| shared | card-battle-arena | $19.52 | $1.38 | $1.68 | $22.58 |
| shared | jenga | $19.52 | $1.38 | $1.68 | $22.58 |
| pixel_grand | pixel-smuggler | $19.52 | $1.38 | $1.68 | $22.58 |
| shared | swanopoly | $66.49 | $2.30 | $1.68 | $70.47 |
| heavy | dependency-hell | $66.49 | $6.90 | $1.68 | $75.07 |
| heavy | dependency-hell-runner | $33.24 | $1.38 | $1.68 | $36.30 |
| pixel_grand | grand-tour-duel | $66.49 | $2.30 | $1.68 | $70.47 |
| | **합계** | **$291.26** | **$17.03** | **$11.76** | **$320.05** |

추가 네 VM만의 14일 합계는 약 **$204.42**다.
합계는 반올림 전 값으로 계산했으므로 표시된 행의 합과 1센트 차이가 날 수 있다.

산식: `336 × (VM 시간당 단가 + 디스크 GiB × 디스크 단가 + IPv4 단가)`.
인터넷/프로젝트 간 전송량, snapshot 백업, 추가 관리형 DB, 로그 초과 사용량 등은 별도다.
따라서 전체 일곱 VM $320.05는
선택한 자원의 고정 사용량 추정이며 최종 청구 상한이 아니다.
이 값은 VM을 켜 둔 테스트 14일 비용이다. 대회 12시간을 그 뒤에 추가하면 별도로 과금된다.
전체 구성을 12시간 더 켜면 약 **$11.43**가 추가되어 총 **$331.48**다.
실제 원화 청구에는 Billing account의 원화 SKU 가격과 세금이 적용된다.
프로젝트를 나눠도 같은 Billing account에 연결하면 같은 크레딧 잔액을 사용한다.

## 수행한 검증과 현재 상태

Terraform 1.15.8 / Google provider 8.8.0에서 `terraform fmt -check`와
`terraform validate`를 통과했다. 기존 shared 세 VM을 보존한 확장 계획으로 적용했으며,
결과는 **28개 생성, 변경·삭제 0개**였다. 적용 후 재계획도 **No changes**였다.
기존 세 VM의 프로젝트와 고정 IP가 유지됨을 확인했다.

GCP API에서 일곱 VM의 `RUNNING`, 각 고정 IP의 `IN_USE`, 설정한 부팅 디스크 크기를 확인했다.
Dependency 본체에 별도 100 GiB 제출물 디스크가 연결된 것도 확인했다.
일곱 VM 모두 service account가 연결되지 않았다.
인증된 GCP guest attributes API의 호스트 공개키로 접속 대상을 확인하고 기존 SLA 개인키로
각 VM의 `sla` 사용자에 SSH 접속했다. 실제 OS는 모두 **Ubuntu 24.04.5 LTS**였다.
접속 정보는 `terraform output vms` 또는 로컬 `deployment-output.json`에서 확인한다.

추가로 shared의 OS Config API와 프로젝트 메타데이터를 Terraform으로 적용했다.
이 적용은 **2개 생성, VM·IAM 변경 0개**였다.
이후 shared 세 VM에서 k3s **v1.36.5+k3s1**과 각 문제 Pod가 이미 실행 중임을 확인했다.
이 실행 상태는 운영 VM에서 별도로 확인한 것이며, Terraform이 문제를 설치한 것은 아니다.
Node Agent 설치와 실제 경기의 12시간 부하 검증은 아직 수행하지 않았다.

## Broker verify의 조회 권한

VM 생성에 사용한 로컬 ADC와 운영 Broker의 attached service account는 별도 인증 주체다.
`central_service_account_email`에는 운영 Broker가 실제 사용하는 서비스 계정을 입력한다.
`iam.tf`는 정유찬 KOTH의 `heavy` 프로젝트와 허준하 KOTH의 `pixel_grand` 프로젝트에만
해당 계정의 `roles/compute.viewer`를 추가한다. 기존 IAM 구성원은 유지한다.
이 역할은 Broker의 verify/sync에 필요한 Compute 조회 권한을 포함한다.
VM 시작·중지에는 별도 제어 권한이 필요하다.
`shared` 프로젝트와 Bootstrap 원격 설치 권한은 이 IAM 리소스의 범위가 아니다.
적용 후 Broker 대시보드에서 각 계정을 다시 verify하고 sync한다.

## 중지와 종료

기존 VM의 컴퓨팅 요금을 중지하려면 Broker 대시보드 `전체 VM`에서 KOTH를 필터링해
VM을 선택한 뒤 `선택 VM 중지`를 사용한다. `problem_vms_running`은 새 VM을 만들 때의
초기 상태만 정하며 Terraform은 기존 VM의 전원 상태 변경을 무시한다.

디스크와 고정 IPv4는 중지 후에도 전체 일곱 VM 기준 약 **$2.06/일**이 발생한다.
이 설정은 14일 뒤 자동 중지하지 않는다. 계속 RUNNING이면 계산한 시간 이후에도 과금된다.

대회 종료 후 백업과 보존 대상을 정한 다음, `main.tf`에서 VM의 `deletion_protection`을
`false`로 바꿔 적용하고 모든 디스크 리소스의 `prevent_destroy` 보호를 해제해야
`terraform destroy`로 전체 정리가 가능하다. 디스크 삭제는 점수와 제출물을 영구 삭제한다.
계속 보존할 디스크는 destroy 대상에서 먼저 분리해 별도 관리한다.

## 공식 근거

- [E2 사양과 제한](https://docs.cloud.google.com/compute/docs/general-purpose-machines)
- [E2 종량제 요금](https://cloud.google.com/products/compute/pricing/general-purpose)
- [디스크 요금](https://cloud.google.com/compute/disks-image-pricing)
- [외부 IPv4와 네트워크 요금](https://cloud.google.com/vpc/network-pricing)
- [Compute quota](https://docs.cloud.google.com/compute/resource-usage)
- [VM metadata SSH 키](https://docs.cloud.google.com/compute/docs/connect/add-ssh-keys)
- [OS Login 역할](https://docs.cloud.google.com/compute/docs/oslogin/set-up-oslogin)
- [로컬 ADC 인증](https://docs.cloud.google.com/docs/authentication/set-up-adc-local-dev-environment)
- [무료 체험과 Billing account](https://docs.cloud.google.com/free/docs/free-cloud-features)
