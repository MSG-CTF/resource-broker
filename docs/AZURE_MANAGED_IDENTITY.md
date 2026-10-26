# Azure 리소스 그룹 범위 자동 연동

Broker GCP VM의 서비스 계정 하나를 여러 Azure 사용자 할당 관리 ID가 신뢰할 수 있다.
Azure 테넌트가 다르면 테넌트마다 관리 ID가 필요하다. 같은 테넌트의 여러 리소스
그룹에는 한 관리 ID를 사용하고 각 그룹에 Reader를 할당할 수 있다.

## Azure와 GCP에서 준비

1. GCP Console → IAM 및 관리자 → 서비스 계정에서 **Broker VM에 연결된** 서비스
   계정의 숫자형 Unique ID를 확인한다. 이메일이나 JSON 키가 아니다.
2. 각 Azure 테넌트에서 Azure 포털 → Managed Identities → Create로 소유한
   리소스 그룹에 **User-assigned managed identity**를 만든다. Client ID를 기록한다.
3. 그 관리 ID → Settings → Federated credentials → Add credential → Other issuer:
   - Issuer: `https://accounts.google.com`
   - Subject: 1단계의 GCP 서비스 계정 Unique ID
   - Audience: `api://AzureADTokenExchange`
4. 조회할 **각 리소스 그룹** → Access control (IAM) → Add role assignment에서
   2단계 관리 ID에 `Reader`를 할당한다. 리소스 그룹마다 별도의 관리 ID를 만들
   필요는 없지만, 테넌트가 다르면 그 테넌트의 관리 ID가 필요하다.

조직의 Azure Policy가 관리 ID 생성이나 역할 할당을 막으면 해당 구독의
관리자에게 요청해야 한다. `Reader`는 VM 조회용이며 Bootstrap Run Command
권한을 포함하지 않는다. 리소스 그룹 범위에서 Subscription 수준 SKU 조회가
거부되면 CPU, 메모리, 아키텍처는 추정하지 않고 미수집으로 표시한다.

## Broker에서 기존 계정 전환

Admin UI → 계정 관리 → 해당 Azure 계정 → **자동 연동 설정**을 연다.
**관리 ID Client ID**와 조회할 그룹을 한 줄에 하나씩 입력한다.

```text
<SUBSCRIPTION_ID>/<RESOURCE_GROUP_NAME>
```

Azure 계정 하나는 하나의 Tenant ID를 나타낸다. 테넌트가 다르면 Broker 계정도
각각 등록한다. 새 계정은 Tenant ID와 Subscription ID로 등록한 뒤 자동으로 열린
Microsoft 로그인 창을 닫고 **자동 연동 설정**을 사용하면 된다. 입력한 모든
그룹의 조회가 성공해야 전환된다. 실패하면 기존
로그인 설정과 VM snapshot은 그대로 유지된다. 성공하면 같은 Broker account ID와
VM ID를 유지하면서 조회 범위 밖의 이전 snapshot은 retired 처리한다.

## API

`PUT /v1/admin/provider-accounts/{account_id}/azure-managed-identity`

- Headers: `Authorization: Bearer <ADMIN_TOKEN>`, `Content-Type: application/json`
- Body:

```json
{
  "client_id": "<MANAGED_IDENTITY_CLIENT_ID>",
  "resource_groups": [
    {"subscription_id": "<SUBSCRIPTION_ID>", "name": "<RESOURCE_GROUP_NAME>"}
  ]
}
```

- `200`: 검증과 동기화 완료. `ProviderAccountSyncResponse`에 `scopes`와 VM 수 포함.
- `400`: Azure 계정이 아니거나 저장된 설정이 잘못됨.
- `401`: Admin 인증 실패.
- `404`: 계정 없음.
- `409`: 진행 중인 Bootstrap job 또는 활성 reservation 존재.
- `422`: Client ID, Subscription ID, 그룹 이름 또는 중복 범위 입력 오류.
- `500`, `503`: DB 또는 서버 구성 오류.
- `502`: Azure 범위 중 하나 이상 조회 실패. `scopes[].error_code`를 확인한다.
  이 경우 기존 계정 설정과 VM snapshot을 변경하지 않는다.

기존 Azure 사용자 로그인과 Subscription 범위 Entra 앱 연동은 계속 지원한다.
새 DB migration은 없다. 실제 token exchange와 VM 조회는 대상 Azure 환경에서
Verify → Sync → VM 목록으로 검증해야 한다.

참고:
- https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-create-trust-user-assigned-managed-identity
- https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-google-cloud
- https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal
