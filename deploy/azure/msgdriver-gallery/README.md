# v5 복구 이미지 Gallery

소스 구독의 `msg` 리소스 그룹에
`msgdriver-clean-v5` 스냅샷을 기반으로 특수화 Windows 11 Gallery 이미지 버전
`msgdrivergallery/msgdriver-v5/1.0.0`을 만듭니다. 원본은 Trusted Launch, Hyper-V V2,
127 GiB이므로 이미지 정의도 해당 속성을 유지합니다.

```powershell
az login --tenant <SOURCE_TENANT_ID>
az account set --subscription <SOURCE_SUBSCRIPTION_ID>
terraform init
terraform plan
terraform apply
terraform output image_version_id
```

이미지 버전은 `koreacentral`의 `Standard_LRS` 복제본 1개만 저장합니다. 다른
리전에서 VM을 만들려면 해당 리전에 이미지 버전 복제본을 추가해야 하며, 지역마다
저장 비용이 증가합니다. 원본 v5 스냅샷은 별도 복구 경로의 기준이므로 이
Terraform은 삭제하지 않습니다. 상태 파일은 비공개로 보관하세요.

다른 테넌트의 사람이나 자동화 앱이 이미지를 사용하려면 소스 Gallery에 대한
읽기 권한과 대상 리소스 그룹의 생성 권한이 각각 필요합니다. 계정·앱 주체를
확인한 뒤 최소 권한으로 부여하세요. 특수화 이미지는 Windows 컴퓨터 이름과
CMID를 원본에서 복제합니다.
