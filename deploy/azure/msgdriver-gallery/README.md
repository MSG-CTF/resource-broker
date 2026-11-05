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

이미지 버전은 현재 Azure에 있는 그대로 `koreacentral`, `koreasouth`, `japaneast`에
`Standard_LRS` 복제본을 각각 1개씩 유지합니다. 리전마다 저장 비용이 발생합니다.
원본 v5 스냅샷은 별도 복구 경로의 기준이므로 이 Terraform은 삭제하지 않습니다.
적용 전 `terraform plan`에서 복제 지역이 제거되거나 이미지가 교체되지 않는지
확인하세요. 상태 파일은 비공개로 보관하세요.

다른 구독은 이 Gallery를 복제하지 않고 위 `image_version_id`를 참조합니다.

다른 테넌트의 사람이나 자동화 앱이 이미지를 사용하려면 소스 Gallery에 대한
읽기 권한과 대상 리소스 그룹의 생성 권한이 각각 필요합니다. 계정·앱 주체를
확인한 뒤 최소 권한으로 부여하세요. 특수화 이미지는 Windows 컴퓨터 이름과
CMID를 원본에서 복제합니다.
