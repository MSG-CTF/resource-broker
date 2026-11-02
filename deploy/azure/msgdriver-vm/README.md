# v5 Gallery 이미지에서 Windows VM 만들기

# `msgdriver-clean-v5`에서 만든 특수화 Gallery 이미지 버전으로 Windows VM 한 대를 만듭니다.
기존 리소스 그룹 안에 전용 VNet, 서브넷, NIC, NSG, 고정 공인 IP, OS 디스크, VM을 만듭니다.
이미 수동으로 만든 VM은
Terraform이 자동으로 관리 대상으로 가져오지 않으므로 `vm_name`을 새 이름으로
지정하세요.

| 항목 | 값 |
|---|---|
| 기본 리전 | `koreacentral` |
| VM 크기 | `Standard_B2als_v2` (2 vCPU, 4 GiB) |
| 이미지 | 소스 구독 `msgdrivergallery/msgdriver-v5/1.0.0` |
| 보안 | vTPM 켜짐, Secure Boot 꺼짐 |
| OS 디스크 | `Standard_LRS`, 127 GiB |

특수화 이미지이므로 관리자 계정, 암호, Windows 컴퓨터 이름과 CMID는 원본에서
유지됩니다. Azure VM 이름만 `vm_name`으로 새로 지정됩니다. Terraform state에
새 관리자 암호를 저장하지 않습니다. 새 VM에서 실행기와 Windows 상태를 검증하세요.
이미지 버전은 고정해 이후 Gallery 업데이트가 기존 VM을 예기치 않게 바꾸지 않도록 합니다.

## 한 구독에 한 대 만들기

Terraform과 Azure CLI가 설치된 PowerShell에서 이 디렉터리로 이동합니다.
구독의 리소스 그룹에 VM·네트워크 생성 권한이 있어야 합니다. 대상 구독/tenant는
`az account show`로 확인한 실제 값을 사용하세요.

```powershell
az login --tenant <SOURCE_TENANT_ID>
az account get-access-token --query tenant -o tsv
az login --tenant <TENANT_ID>
az account get-access-token --query tenant -o tsv
az account set --subscription <SUBSCRIPTION_ID>
az account show --query "{subscription:id,tenant:tenantId}" -o json

$env:TF_VAR_tenant_id = '<TENANT_ID>'
$env:TF_VAR_subscription_id = '<SUBSCRIPTION_ID>'
$env:TF_VAR_resource_group_name = '<RESOURCE_GROUP_NAME>'
$env:TF_VAR_vm_name = 'msgdriver01'
$env:TF_VAR_source_image_version_id = '<SOURCE_IMAGE_VERSION_ID_FROM_GALLERY_OUTPUT>'

$stateDir = Join-Path $HOME "msgdriver-terraform-state/$env:TF_VAR_subscription_id/$env:TF_VAR_vm_name"
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$env:TF_DATA_DIR = Join-Path $stateDir '.terraform'
terraform init -backend-config="path=$(Join-Path $stateDir 'terraform.tfstate')"
terraform plan
terraform apply
terraform output public_ip
```

VM 생성 뒤 Azure VM Run Command에서 빌드와 Secure Boot를 확인합니다.

```powershell
$check = @'
$v = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
"Build: 10.0.$($v.CurrentBuildNumber).$($v.UBR)"
"Secure Boot: $(Confirm-SecureBootUEFI)"
'@
az vm run-command invoke --resource-group $env:TF_VAR_resource_group_name --name $env:TF_VAR_vm_name --command-id RunPowerShellScript --scripts $check --query "value[0].message" -o tsv
```

빌드와 Secure Boot 결과는 원본 v5 이미지와 비교하세요. 또한 실행기 443/tcp가
기대대로 응답하는지 별도로 확인해야 합니다.

`plan`에서 기존 리소스의 삭제/교체가 보이면 적용하지 마세요. 구독마다 별도의
Terraform state를 사용해야 합니다. 같은 VM을 다시 관리할 때는 **같은 state 경로와
`TF_DATA_DIR`**를 설정하세요. 다른 구독/VM으로 전환할 때는 그 대상의 state 경로를
지정해 `terraform init -reconfigure -backend-config="path=..."`을 실행하고,
`terraform plan`에 기존 VM 삭제/교체가 없는지 확인하세요. state 파일과 백업, plan,
`.terraform/`에는 민감정보가 남을 수 있으므로 Git에 추가하거나 타인에게 전달하지
마세요.
AzureRM provider는 리소스 공급자 자동 등록을 끕니다. 대상 구독의
`Microsoft.Compute`, `Microsoft.Network`가 등록되지 않았다면 구독 소유자가
등록해야 합니다.

기본 설정은 고정 공인 IP를 만들지만, 할당량이 부족하거나 사설망 검증만 할 때는
`public_ip_enabled = false`로 만들지 않을 수 있습니다. NSG에 인바운드 허용
규칙을 추가하지 않습니다.
3389/tcp RDP도 열지 않습니다. 운영자 접속이 필요하면 별도 경로를 준비하세요.
이 Terraform은 v5 이미지 내용을 그대로 복제하며, VM마다 고유해야 하는
인증서·토큰이나 Windows 식별자를 새로 발급하지 않습니다.

## msgdriver Windows 실행기 기준 이미지

Terraform은 VM과 네트워크 및 OS 디스크를 만듭니다. 원본은 Azure `msg` 리소스
그룹의 `msgdriver-clean-v5` 스냅샷으로 만든 Gallery 이미지 버전입니다. v5를
여러 계정에 복제하기 전에 원본 이미지에 VM 고유 인증서·토큰이 남아 있는지와
실행기 동작을 실제 VM에서 확인해야 합니다.

Runtime 실행기 소스 변경은 [`runtime-mtls.patch`](runtime-mtls.patch)에 있습니다.
Windows VM의 443/tcp API는 Runtime 클라이언트 인증서를 요구합니다. Runtime의
개인키는 Runtime 서버에서만 보관하고, 공개 신뢰 인증서만 기준 이미지에 넣습니다.
OS 디스크를 Gallery 이미지 버전에서 복구한 뒤 Broker는 Azure 제어 API로 VM의 현재 공인 IP를
조회하고 Azure Run Command로 서버 인증서를 새로 생성합니다. Broker는 서버의
공개 인증서를 인증된 Windows VM 연결 응답에 넣은 다음 VM을 `READY`로 바꿉니다.
Runtime 클라이언트는 그 인증서 한 장과 대상 IP를 검증해 EXE를 보냅니다.

복원 과정은 새 OS 디스크가 연결되고 실행기 상태 확인이 성공한 뒤 분리된 이전
OS 디스크를 삭제합니다. 복원용 Azure identity에는 대상 디스크에 대한
`Microsoft.Compute/disks/delete` 권한이 필요합니다. 삭제에 실패하면 VM은
`RESET_REQUIRED`로 남고 같은 실행을 재시도합니다. 이 변경 전 복원에서 남은
디스크는 자동 정리 대상이 아니므로 Azure **Disks**에서 연결 상태와 필요 여부를
직접 확인해야 합니다.

다른 구독으로 확장하려면 소스 Gallery 읽기 권한, 대상 구독의 VM·디스크 권한,
Broker의 계정별 연합 인증 및 대상 VM 등록을 검증해야 합니다. VM별 443/tcp NSG
인바운드 규칙도 필요합니다.

### 복원 완료 알림

Broker의 `WINDOWS_RECOVERY_RUNTIME_URL`에는 Runtime API의 origin(주소와 포트)을 설정하고
`WINDOWS_RECOVERY_TOKEN`에는 Runtime이 발급한 전용 Bearer token을 설정합니다.
이 token은 Broker의 `RUNTIME_API_TOKEN`과 별개입니다. Broker는 VM 복원이
성공해 `READY`를 저장한 뒤 다음 요청을 보냅니다.

```text
POST /internal/v1/windows-vms/{resource_target_id}/recovery-events
Authorization: Bearer <Windows recovery token>
Content-Type: application/json

{"execution_id":"<Runtime execution UUID>","event_id":"<stable event UUID>","vm_state":"READY"}
```

Broker는 2xx 응답을 받으면 전달 완료 시각을 기록합니다. 실패하면 DB에 저장한
같은 `event_id`로 30초 뒤 재시도하며, Broker 재시작 후에도 미전달 건을 다시
보냅니다. Runtime은 동일 이벤트 재전송을 중복 처리 없이 승인해야 합니다.
Runtime API와 token이 배포되기 전에는 알림이 대기 상태로 남습니다.
이 기능 배포 전에 이미 `READY`가 된 실행에는 이벤트가 자동 생성되지 않습니다.

2026-10-27 canary에서 Runtime 서비스 계정이 무해한 EXE를 실행해 `succeeded`,
종료 코드 0을 받았습니다. 그 실행의 Broker `finished`부터 다음 `READY`까지
332.707초(약 5분 33초)였습니다. 실패 실행의 복구는 369.478초(약 6분 9초)였습니다.
이는 두 번의 실제 측정값이며 25대 동시 복구 시간이나 운영 보장 시간은 아닙니다.

현재 Windows 실행기는 받은 EXE를 `ctfuser`로 직접 실행합니다.
`C:\ctf\run-payload.ps1`을 경유하지 않으므로 그 스크립트의 10 MiB 제한과
동일한 계약이라고 보아서는 안 됩니다. 업로드 Backend와의 자동 연결도 별도 작업입니다.

기존 [`install-executor.ps1`](install-executor.ps1)과
[`install-runtime-token.ps1`](install-runtime-token.ps1)은 사용하지 않는 이전 시제품입니다.
특히 Broker `RUNTIME_API_TOKEN`을 Windows VM에 설치하지 마세요.
