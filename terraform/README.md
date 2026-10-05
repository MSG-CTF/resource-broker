# GCP 다중 Project IAM 온보딩

이 Terraform은 기존 중앙 MSG Broker Service Account를 여러 GCP Project의 IAM에
한 번에 등록하고, 연결된 Cloud Billing Account의 비용 정보를 읽을 수 있게
준비합니다.

`google_project_iam_member`를 사용하므로 기존 Project IAM policy 전체를
덮어쓰지 않고 필요한 member만 추가합니다. Billing 권한도
`google_billing_account_iam_member`로 기존 member를 보존하면서 추가합니다.
Service Account, Project, VM, firewall, Billing export 자체를 새로 만들지는
않습니다. 대신 현재 Broker 동작에 필요한 Google API와 VM Manager 설정은 모든
대상 Project에 필수 적용합니다.

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
| 각 대상 Billing Account | `roles/billing.viewer` | 비용, 사용량 및 거래 정보 읽기 |

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
상세 비용 코드에 필요한 `roles/bigquery.dataViewer`와
`roles/bigquery.jobUser`도 모든 대상 Project에 미리 부여합니다. 이후 각 Billing
Account의 Billing Export를 대상 Project의 BigQuery dataset에 활성화하면 중앙
Service Account가 query할 수 있습니다. 현재 Terraform은 아직 존재하지 않을 수
있는 export dataset 자체는 임의로 만들지 않습니다.

## 사전 조건

1. 중앙 Service Account가 중앙 Project에 이미 존재해야 합니다.
2. Terraform을 실행하는 사용자/CI identity는 **각 대상 Project**에서 IAM policy를
   읽고 수정할 수 있어야 합니다. 일반적으로 온보딩 작업자에게만
   `roles/resourcemanager.projectIamAdmin`이 필요합니다.
3. 실행 identity는 각 대상 Billing Account의 IAM policy도 읽고 수정할 수 있어야
   합니다. 각 Billing Account의 기존 관리자에게 이 권한이 있어야 합니다.
4. Project가 서로 다른 개인 계정이나 Organization에 속해 있으면 하나의 실행
   identity가 20개 Project 모두에서 위 권한을 가져야 합니다. 그렇지 않으면 각
   소유자가 자신의 Project ID만 넣어 동일한 중앙 Service Account를 온보딩해야
   합니다.
5. 대상 VM에는 Service Account와 OS Config Agent가 있어야 합니다. 최신 지원
   Google Ubuntu image에는 Agent가 기본 설치되지만 오래된 image나 custom image는
   별도 설치가 필요합니다.
6. private VM은 Google API에 접근할 수 있도록 Private Google Access가 필요하고,
   모든 대상 VM은 Bootstrap/Agent 통신을 위해 중앙 Gateway HTTPS endpoint로
   outbound 연결할 수 있어야 합니다.
7. Terraform `>= 1.5`와 Google Cloud CLI가 필요합니다.

## 최초 적용 명령 (PowerShell)

저장소 루트에서 실행합니다.

```powershell
cd terraform
Copy-Item terraform.tfvars.example terraform.tfvars
notepad terraform.tfvars
```

`terraform.tfvars`에서 다음 값을 실제 값으로 바꿉니다.

- `central_service_account_email`: 중앙 Broker Service Account 이메일
- `target_project_ids`: 관리할 20개 Project ID
- `target_billing_account_ids`: 대상 Project가 연결된 Cloud Billing Account ID

키 파일을 만들 필요 없이 온보딩 권한이 있는 사용자로 Application Default
Credentials(ADC)에 로그인합니다.

```powershell
gcloud auth application-default login
gcloud auth list
terraform init
terraform fmt -check
terraform validate
terraform plan -out=iam.tfplan
terraform apply iam.tfplan
```

`terraform plan`에서 Project 수, Billing Account 수와 역할 수를 확인한 뒤에만
apply합니다. 중앙 Service Account에는 Project마다 7개의 IAM member가 생성되고,
Billing Account마다 `roles/billing.viewer` 1개가 생성됩니다. 별도로 각 Project의
Google-managed OS Config Service Agent에 `roles/osconfig.serviceAgent`가
부여됩니다. Compute, OS Config, Cloud Billing, BigQuery API와
`enable-osconfig=TRUE` metadata도 Terraform이 직접 적용합니다.

## 적용 결과 확인

Terraform이 관리하는 Project/역할 목록을 확인합니다.

```powershell
terraform output target_project_ids
terraform output granted_roles_by_project
terraform output target_billing_account_ids
terraform output granted_billing_roles_by_account
terraform output enabled_services_by_project
terraform output osconfig_metadata_by_project
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

Billing Account 하나의 실제 IAM binding도 확인합니다.

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
terraform plan -destroy -out=iam-destroy.tfplan
terraform apply iam-destroy.tfplan
```

이 명령은 이 state가 관리하는 IAM member만 제거하며 Project나 VM을 삭제하지
않습니다. 단, Broker는 즉시 해당 Project의 Compute resource를 관리할 수 없게
됩니다.

## State 주의

`terraform.tfvars`, `*.tfstate`, `*.tfplan`, `.terraform/`은 로컬 실행 산출물로
취급하고 Git에 커밋하지 마세요. 팀 운영에서는 IAM이 제한되고 암호화/잠금이
적용된 원격 backend로 state를 옮기는 것을 권장합니다. Service Account JSON key는
Terraform 변수나 state에 넣지 않습니다.
