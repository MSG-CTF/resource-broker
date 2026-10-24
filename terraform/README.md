# GCP 다중 Project IAM 및 문제 VM 생성

이 Terraform은 기존 중앙 MSG Broker Service Account를 여러 GCP Project의 IAM에
한 번에 등록하고 API와 VM Manager를 준비하며 Project마다 문제 VM 네 대를 만듭니다.
Cloud Billing Account의 IAM
등록은 `manage_billing_iam = true`일 때만 수행하며 기본값은 `false`입니다.

`google_project_iam_member`를 사용하므로 기존 Project IAM policy 전체를
덮어쓰지 않고 필요한 member만 추가합니다. Billing IAM을 활성화하면
`google_billing_account_iam_member`로 기존 member를 보존하면서 추가합니다.
중앙 Service Account, Project, Billing export 자체는 새로 만들지 않습니다.
`main.tf`가 VM, 디스크, 고정 공인 IP와 전용 VPC·서브넷·방화벽을 생성합니다.
이제 같은 디렉터리의 plan/apply에는 IAM과 VM 생성이 모두 포함됩니다.

## 문제 VM 구성

| Project당 VM | 사양 | OS | 부팅 디스크 |
|---|---|---|---|
| `msg-problem-heavy-01` | `e2-standard-8`: 8 vCPU / 32 GiB | Ubuntu 24.04 LTS AMD64 | `pd-balanced` 100 GiB |
| `msg-problem-light-01` | `e2-standard-4`: 4 vCPU / 16 GiB | Ubuntu 24.04 LTS AMD64 | `pd-balanced` 100 GiB |
| `msg-problem-light-02` | `e2-standard-2`: 2 vCPU / 8 GiB | Ubuntu 24.04 LTS AMD64 | `pd-balanced` 50 GiB |
| `msg-problem-light-03` | `e2-standard-2`: 2 vCPU / 8 GiB | Ubuntu 24.04 LTS AMD64 | `pd-balanced` 50 GiB |

Project당 합계는 16 vCPU, RAM 64 GiB, 공인 IPv4 네 개, 부팅 디스크 300 GiB입니다.
현재 두 Project면 VM 여덟 대입니다. 테스트 Project에서 조회한 전체 리전 CPU 한도는
16 vCPU, 리전 SSD 계열 디스크 한도는 300 GiB여서 기존 두 VM의 디스크 100 GiB는
유지하고 추가 두 VM의 디스크를 각각 50 GiB로 구성합니다. 다른 Project의 한도가
동일하다고 가정하지 마세요. 기존 사용량과 CPU·IP·디스크 quota에 따라 생성이
실패할 수 있으며, VM·디스크·공인 IP는 생성 후 비용 또는 무료 크레딧을 소비합니다.

문제 수는 VM 전체 사양이 아니라 시스템 사용량과 문제별 자원 요구량을 차감해
계산합니다. 아래는 VM마다 시스템용 0.5 vCPU·RAM 2 GiB를 제외한다고 가정한
계획용 예시이며, Terraform이나 K3s에 이 예약량을 설정한 것은 아닙니다.

| 문제 인스턴스 하나의 요구량 | heavy-01 | light-01 | light-02 | light-03 | Project당 합계 |
|---|---:|---:|---:|---:|---:|
| 2 vCPU / RAM 4 GiB | 3 | 1 | 0 | 0 | 4 |
| 1 vCPU / RAM 2 GiB | 7 | 3 | 1 | 1 | 12 |
| 0.5 vCPU / RAM 0.5 GiB | 15 | 7 | 3 | 3 | 28 |

각 행은 해당 문제만 실행하는 경우이며 행끼리 합산하지 않습니다. CPU와 RAM으로만
계산한 예시이므로 이미지·로그·ephemeral storage, Pod overhead, 실제 시스템 부하를
반영하면 개수가 줄 수 있습니다. 최종 배치는 Agent observation과 Broker reservation을
기준으로 판단하며 이 표는 동시 실행을 보장하지 않습니다.

각 Project에 독립 VPC와 `10.80.0.0/24` 서브넷을 만듭니다. 서로 연결하지 않으므로
Project 간 같은 CIDR을 사용하며 K3s 기본 pod/service CIDR과는 겹치지 않습니다.
요청한 대로 `msg-problem` 태그 VM에 TCP/UDP 1–65535를 `0.0.0.0/0`으로 공개합니다.
이 규칙은 SSH·K3s API·UDP 8472 같은 관리/노드 통신 포트도 포함하므로 인증서만으로
모든 공개 포트가 보호되는 것은 아닙니다.

VM에는 각 Project의 기존 Compute 기본 Service Account를 조회해 연결하며 중앙
Broker Service Account를 연결하지 않습니다. 기존 계정의 IAM 역할은 변경하지 않고
`cloud-platform` OAuth scope를 설정합니다. 기본 계정에 Editor 같은 광범위 권한이
남아 있으면 VM의 workload도 그 권한에 접근할 수 있으므로 적용 전 확인하세요.

Ubuntu 이미지의 OS Config Agent는 Project metadata의 `enable-osconfig=TRUE`를
상속합니다. Terraform은 k3s나 Node Agent 설치 token/key를 VM metadata에 넣지 않습니다.

## 권한 구성

| 적용 위치 | 추가되는 역할 | 의미 |
|---|---|---|
| 각 대상 Project | `roles/compute.admin` | VM 및 firewall을 포함한 Compute Engine 전체 관리 |
| 각 대상 Project | `roles/osconfig.osPolicyAssignmentAdmin` | Broker의 OS Policy 생성·삭제 |
| 각 대상 Project | `roles/osconfig.osPolicyAssignmentReportViewer` | OS Policy 적용 결과 조회 |
| 각 대상 Project | `roles/iam.serviceAccountUser` | VM에 해당 Project의 Service Account를 연결하는 `actAs` 권한 |
| 각 대상 Project | `roles/serviceusage.serviceUsageAdmin` | Broker SA가 필요한 Google API를 활성화·확인할 수 있는 권한 |
| 각 대상 Project | `roles/bigquery.dataViewer` | 향후 Billing export dataset을 포함한 BigQuery 데이터 읽기 |
| 각 대상 Project | `roles/bigquery.jobUser` | 향후 비용 집계 BigQuery query 실행 |
| 각 대상 Billing Account (`manage_billing_iam = true`일 때만) | `roles/billing.viewer` | 비용, 사용량 및 거래 정보 읽기 |

Terraform은 추가로 각 대상 Project에서 다음을 구성합니다.

- Compute Engine API 활성화
- OS Config API 활성화
- Cloud Billing API 활성화
- BigQuery API 활성화
- Google-managed OS Config Service Agent 생성
- 위 Service Agent에 `roles/osconfig.serviceAgent` 부여
- Project metadata `enable-osconfig=TRUE` 적용

`roles/compute.admin`은 Project IAM을 변경하는 `Project IAM Admin` 권한은
포함하지 않습니다. Broker 자체에는 `roles/resourcemanager.projectIamAdmin`이나
`roles/owner`를 부여하지 마세요.

OS Policy는 대상 VM에서 root 수준 코드를 실행할 수 있고, Project 범위의
`roles/iam.serviceAccountUser`는 Project 안의 Service Account를 VM에 연결할 수
있게 합니다. 요청한 운영 기준에 따라 둘 다 모든 대상 Project에 필수 적용합니다.

`roles/billing.viewer`는 Cloud Billing Account의 비용 정보를 읽는 권한입니다.
현재는 비용 조회를 미루므로 `manage_billing_iam = false`로 두고 결제 계정 IAM의
조회·수정을 건너뜁니다. Project별 BigQuery 역할과 API 준비 설정은 기존대로
유지하며, 이 옵션은 Billing Account IAM 등록만 제어합니다. 이후 각 Billing
Account의 Billing Export를 대상 Project의 BigQuery dataset에 활성화하면 중앙
Service Account가 query할 수 있습니다. 현재 Terraform은 아직 존재하지 않을 수
있는 export dataset 자체는 임의로 만들지 않습니다.

## 사전 조건

1. 중앙 Service Account가 중앙 Project에 이미 존재해야 합니다.
2. Terraform을 실행하는 사용자/CI identity는 **각 대상 Project**에서 IAM policy를
   읽고 수정할 수 있어야 합니다. `roles/resourcemanager.projectIamAdmin`만으로는
   이 코드 전체를 적용할 수 없습니다. API 활성화, OS Config Service Agent 생성,
   Compute Project metadata 변경 권한도 필요합니다.
3. `manage_billing_iam = true`인 경우에만 실행 identity가 각 대상 Billing
   Account의 IAM policy를 읽고 수정할 수 있어야 합니다. 기본값 `false`에서는
   Billing IAM을 관리하지 않으므로 이 권한은 필요하지 않습니다.
4. Project가 서로 다른 개인 계정이나 Organization에 속해 있으면 하나의 실행
   identity가 모든 대상 Project에서 위 권한을 가져야 합니다. 그렇지 않으면 각
   소유자가 자신의 Project ID만 넣어 동일한 중앙 Service Account를 온보딩해야
   합니다.
5. 대상 VM에는 Service Account와 OS Config Agent가 있어야 합니다. 최신 지원
   Google Ubuntu image에는 Agent가 기본 설치되지만 오래된 image나 custom image는
   별도 설치가 필요합니다.
6. private VM은 Google API에 접근할 수 있도록 Private Google Access가 필요하고,
   모든 대상 VM은 Bootstrap/Agent 통신을 위해 중앙 Gateway HTTPS endpoint로
   outbound 연결할 수 있어야 합니다.
7. Terraform `>= 1.5`와 Google Cloud CLI가 필요합니다.
8. VM 생성 실행 계정에는 Compute VM·디스크·네트워크·서브넷·공인 IP·방화벽 생성
   권한과 VM에 연결할 기존 Service Account의 `iam.serviceAccounts.actAs`가 필요합니다.
   앞서 Broker Service Account에 부여한 역할은 Terraform 실행 사용자에게 자동으로
   부여되지 않습니다. 이 코드는 실행 계정에 역할을 추가하거나 인증 계정을 바꾸지 않습니다.

## 최초 적용 명령 (PowerShell)

저장소 루트에서 실행합니다.

```powershell
cd terraform
notepad projects.tfvars
```

`projects.tfvars`의 예시를 실제 값으로 바꾸고 Project별 블록을 추가합니다.
파일을 새로 준비할 때는 `terraform.tfvars.example`을 복사해 사용할 수 있습니다.

- `central_service_account_email`: 중앙 Broker Service Account 이메일
- `manage_billing_iam`: 기본값 `false`. 비용 조회 권한을 나중에 등록할 때만 `true`
- `problem_vms_running`: 기본값 `true`. `false`면 VM을 삭제하지 않고 중지
- `projects`의 key: 관리할 Project ID
- `projects`의 각 값: 해당 Project의 기존 `billing_account_id`, `region`, `zone`

같은 Billing Account를 쓰는 Project에는 같은 ID를 적습니다. Terraform이 중복을
제거해 `manage_billing_iam = true`일 때 Billing Account마다 IAM member를 하나씩
관리합니다. `false`여도 ID는 목록에 보관합니다. 이 값으로 Project의 결제 연결을
변경하지 않습니다. `region`과 `zone`은 실제 VM 생성 위치입니다.

`projects.tfvars`는 자동 로드되는 파일명이 아니므로 계획을 만들 때
`-var-file="projects.tfvars"`를 지정합니다. 기존 `target_project_ids`와
`target_billing_account_ids` 입력은 `projects`로 통합됐습니다. 이전 설정을 쓰고
있었다면 같은 Project/Billing Account ID를 새 형식으로 옮기세요. 기존 resource
주소와 output 이름은 유지됩니다. 관리 중인 Project를 목록에서 빼거나 관리 중인
Billing Account의 마지막 참조를 빼면 다음 apply에서 해당 IAM 권한 등이 회수되므로
삭제 계획을 확인하세요. 이제 Project를 빼면 그 Project의 VM·디스크·IP·네트워크도
삭제 대상이 됩니다.

키 파일을 만들 필요 없이 온보딩 권한이 있는 사용자로 Application Default
Credentials(ADC)에 로그인합니다.

```powershell
gcloud auth application-default login
terraform init
terraform fmt -check
terraform validate
terraform plan "-var-file=projects.tfvars" "-out=iam.tfplan"
terraform apply iam.tfplan
```

ADC 로그인 화면에서 온보딩 권한이 있는 실행 계정을 선택합니다. `gcloud auth list`의
활성 CLI 계정과 Terraform이 사용하는 ADC 계정은 별개일 수 있습니다.

`terraform plan`에서 Project 수, Billing Account 수와 역할 수를 확인한 뒤에만
apply합니다. 중앙 Service Account에는 Project마다 7개의 IAM member가 생성됩니다.
기본값에서는 Billing IAM member를 생성하지 않으며, `manage_billing_iam = true`일
때만 Billing Account마다 `roles/billing.viewer` 1개가 생성됩니다. 별도로 각 Project의
Google-managed OS Config Service Agent에 `roles/osconfig.serviceAgent`가
부여됩니다. Compute, OS Config, Cloud Billing, BigQuery API와
`enable-osconfig=TRUE` metadata도 Terraform이 직접 적용합니다.

이전 Billing IAM 403 오류 후 다시 적용할 때는 기존 plan을 재사용하지 말고 위
`terraform plan` 명령으로 새 계획을 만드세요. 이미 state에서 관리하던 Billing IAM이
있다면 `false` 전환 시 권한 회수 계획이 생길 수 있고 조회·수정 권한도 여전히 필요할
수 있습니다. 기존 state를 확인한 뒤 적용하세요.

Broker 비용 조회는 별도 API와 UI 버튼으로 동작하며 VM verify/sync와 분리돼
있습니다. 이번 변경에는 Broker 기능 코드 수정이나 재배포가 필요하지 않습니다.

## IAM 적용 후 VM 생성

`projects.tfvars`에는 테스트할 두 Project만 유지하고 `manage_billing_iam = false`로
둡니다. 같은 `terraform` 디렉터리에서 새 plan을 만듭니다.

```powershell
terraform validate
terraform plan "-var-file=projects.tfvars" "-out=vm.tfplan"
```

새 Project 두 개라면 VM 8개, 고정 공인 IP 8개, VPC 2개, 서브넷 2개, 방화벽 2개가
생성 대상입니다. Project별 heavy-01과 light-01을 이미 만든 경우에는 기존 VM과 IP의
Terraform 주소·이름을 유지하며 VM 4개와 IP 4개만 추가합니다. 기존 리소스의 삭제나
교체가 없는지 확인한 다음 적용합니다.

```powershell
terraform apply vm.tfplan
terraform output problem_vms
```

이전 `iam.tfplan`에는 새 VM 코드가 들어 있지 않으므로 재사용하지 않습니다.
이후 Broker에서 두 Project를 포함한 계정을 등록하고 verify/sync하여 VM을 수집한 뒤
각 VM에 Bootstrap INSTALL 작업을 실행합니다. 기존 Bootstrap이 k3s·gVisor·Node Agent를
설치하며, Agent 관측과 ready를 확인한 뒤 운영 후보로 활성화합니다. VM 생성만으로
Agent가 설치되거나 Runtime ready가 되는 것은 아닙니다.

## 문제 VM 일시 중지와 다시 시작

`projects.tfvars`에서 `problem_vms_running = false`로 설정하면 이 state가 관리하는
문제 VM의 `desired_status`를 `TERMINATED`로 변경합니다. GCP에서 이 값은 삭제가
아닌 중지 상태이며 디스크, 고정 공인 IP, 네트워크와 IAM은 유지합니다. 메모리의
프로세스 상태는 보존하지 않습니다. 이 state가 관리하지 않는 VM은 대상이 아닙니다.

```powershell
terraform plan "-var-file=projects.tfvars" "-out=vm-stop.tfplan"
```

중지만 하려면 계획이 `0 to add, N to change, 0 to destroy`이고, 변경 항목이 기존
문제 VM의 `desired_status`뿐인지 확인합니다. 중지 옵션은 생성 여부를 제어하지
않으므로 아직 생성하지 않은 VM이 구성에 있으면 일반 plan에 생성 작업도 포함됩니다.
`-target`으로 기존 VM만 지정해도 의존하는 미생성 IP가 생성 대상에 포함될 수 있습니다.
이 경우 일반 plan을 적용하지 않습니다. 이번 `vm-stop.tfplan`은 state에 존재하는
문제 VM 전체만 `local.problem_vms`에 넣는 임시 override로 만들고, 생성·삭제가 없는지
검증한 중지 전용 계획입니다. 계획 생성 후 override 파일은 제거하며 기존 state는
직접 수정하지 않습니다. 이후 계획을 다시 만들 때도 미생성 VM/IP를 제외한 범위를
확인하고, 정상 배포 전에는 원래 구성의 전체 plan을 다시 확인합니다.

```powershell
terraform apply vm-stop.tfplan
terraform output problem_vms
```

적용 후 VM의 `current_status`가 `TERMINATED`인지 확인합니다. 다시 켤 때는
`projects.tfvars`의 `problem_vms_running = true`로 바꾸고 새 plan을 만들어 적용합니다.
미생성 VM을 추가하고 싶지 않으면 다시 시작할 때도 같은 기존 VM만 대상으로 잡습니다.
중지할 때 Project를 목록에서 빼거나 `terraform destroy`를 사용하지 마세요.
이전에 저장한 생성/시작 plan은 현재 중지 설정을 반영하지 않으므로 재사용하지 않습니다.

CPU와 RAM의 실행 요금은 중지 후 멈추지만 디스크와 고정 공인 IPv4는 계속 과금됩니다.
현재 `us-central1` 정가 기준으로 VM 네 대·디스크 300 GiB·고정 IP 네 개를 보유하면
중지 중에도 Project당 약 $1.47/일입니다. 기존 VM 두 대·디스크 200 GiB·IP 두 개만
있으면 약 $0.90/일이며 트래픽·세금 등은 별도입니다.
[중지 동작 및 과금](https://docs.cloud.google.com/compute/docs/instances/stop-start-instance),
[디스크 요금](https://cloud.google.com/compute/disks-image-pricing),
[공인 IPv4 요금](https://cloud.google.com/vpc/network-pricing#ipaddress).

## 적용 결과 확인

Terraform이 관리하는 Project/역할 목록을 확인합니다.

```powershell
terraform output target_project_ids
terraform output granted_roles_by_project
terraform output target_billing_account_ids
terraform output granted_billing_roles_by_account
terraform output enabled_services_by_project
terraform output osconfig_metadata_by_project
terraform output problem_vms
```

Project 하나의 실제 IAM binding을 확인합니다.

```powershell
$project = "target-project-01"
$serviceAccount = "msg-broker-controller@central-broker-project.iam.gserviceaccount.com"

gcloud projects get-iam-policy $project `
  --flatten="bindings[].members" `
  --filter="bindings.role=roles/compute.admin AND bindings.members=serviceAccount:$serviceAccount" `
  --format="table(bindings.role,bindings.members)"
```

출력에 `roles/compute.admin`과 중앙 Service Account가 한 행으로 표시되면 IAM
온보딩이 완료된 것입니다. 실제 Broker VM/GKE workload에서는 JSON key 대신
attached Service Account와 Metadata ADC를 사용합니다.

`manage_billing_iam = true`로 적용한 경우에만 Billing Account IAM binding도 확인합니다.

```powershell
$billingAccount = "000000-000000-000000"

gcloud billing accounts get-iam-policy $billingAccount `
  --flatten="bindings[].members" `
  --filter="bindings.role=roles/billing.viewer AND bindings.members=serviceAccount:$serviceAccount" `
  --format="table(bindings.role,bindings.members)"
```

## 권한 회수

먼저 삭제 계획을 확인합니다.

```powershell
terraform plan -destroy "-var-file=projects.tfvars" "-out=iam-destroy.tfplan"
terraform apply iam-destroy.tfplan
```

이 명령은 이 state가 관리하는 VM과 부팅 디스크·고정 공인 IP·전용 네트워크·방화벽,
IAM member와 `enable-osconfig` Project metadata 등을 삭제합니다. Project 자체와
활성화된 API는 유지됩니다. VM에 저장한 데이터는 부팅 디스크와 함께 삭제됩니다.
VM을 중지만 하면 디스크와 고정 IP는 남으며, 이 명령은 중지가 아닌 리소스 삭제입니다.

## State 주의

`projects.tfvars`, `terraform.tfvars`, `*.tfstate`, `*.tfplan`, `.terraform/`은 로컬 실행 산출물로
취급하고 Git에 커밋하지 마세요. 팀 운영에서는 IAM이 제한되고 암호화/잠금이
적용된 원격 backend로 state를 옮기는 것을 권장합니다. Service Account JSON key는
Terraform 변수나 state에 넣지 않습니다.
