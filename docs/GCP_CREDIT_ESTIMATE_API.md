# GCP 추정 잔여 크레딧 API

Admin 계정 관리 화면이 GCP 프로모션 크레딧의 **추정 잔액**을 설정하고 조회하는 API다. Google Cloud Credits 화면의 현재 `Remaining value`를 기준값으로 저장하고, 그 시점 이후 BigQuery Billing Export에 기록된 `PROMOTION` 크레딧 사용액을 차감한다.

Google Cloud의 공식 잔여 크레딧 값을 직접 반환하는 공개 API가 아니므로 응답의 `estimated_remaining_credit`는 추정치다.

## 사전 조건

- Billing Account의 Standard usage cost export가 BigQuery dataset으로 활성화돼 있어야 한다.
- 설정 저장 전에 export가 현재 시점까지 반영됐는지 확인한다.
- Broker Service Account에는 export project의 `roles/bigquery.jobUser`, export dataset을 읽을 수 있는 `roles/bigquery.dataViewer`가 필요하다.
- 이 저장소의 Terraform은 대상 project 수준의 두 BigQuery role과 Billing Account의 `roles/billing.viewer`를 부여한다. Dataset이 다른 project에 있으면 그 export project도 Terraform 대상에 포함해야 한다.
- 한 Broker GCP 계정 레코드는 하나의 Billing Account 기준 잔액을 추적한다.
- Service Account key 파일은 사용하지 않고 Broker 런타임의 Application Default Credentials를 사용한다.

Standard export table 이름은 다음 규칙으로 계산한다.

```text
<export-project>.<dataset>.gcp_billing_export_v1_<BILLING_ACCOUNT_ID의 하이픈을 밑줄로 변경>
```

## 공통 인증

두 API 모두 기존 Admin 인증을 사용한다.

```http
Authorization: Bearer <ADMIN_TOKEN>
Accept: application/json
```

쓰기 요청은 다음 헤더도 보낸다.

```http
Content-Type: application/json
```

외부 Gateway를 통하면 경로 앞에 `/api`를 붙이고, Broker 앱을 직접 호출하면 `/api` 없이 `/v1/...`을 사용한다.

## 기준값 저장 또는 재설정

- Method: `PUT`
- Internal path: `/v1/admin/provider-accounts/{account_id}/gcp-credit-settings`
- Gateway path: `/api/v1/admin/provider-accounts/{account_id}/gcp-credit-settings`
- Success: `200 OK`

`baseline_at`은 클라이언트가 보내지 않는다. 서버가 요청을 저장한 현재 UTC 시각으로 설정한다. 같은 API를 다시 호출하면 기준 잔액과 기준 시각이 모두 교체된다.

### Request body

```json
{
  "billing_account_id": "000000-000000-000000",
  "billing_export_project_id": "example-billing-export",
  "billing_export_dataset_id": "billing_export",
  "baseline_remaining_credit": "1000.00",
  "currency": "USD"
}
```

| 필드 | 규칙 | 설명 |
| --- | --- | --- |
| `billing_account_id` | `XXXXXX-XXXXXX-XXXXXX`, 숫자·대문자 | Credits와 export가 속한 Billing Account |
| `billing_export_project_id` | GCP project ID | BigQuery dataset이 있는 project |
| `billing_export_dataset_id` | BigQuery dataset ID | Standard usage cost export dataset |
| `baseline_remaining_credit` | 0 이상, 최대 소수 9자리 | 저장 직전 Credits 화면의 `Remaining value` |
| `currency` | 대문자 3자리 | 기준값과 Billing Export가 사용하는 동일한 통화, 예: `USD` |

### 200 response

```json
{
  "account_id": "00000000-0000-4000-8000-000000000001",
  "credit_tracking": {
    "billing_account_id": "000000-000000-000000",
    "billing_export_project_id": "example-billing-export",
    "billing_export_dataset_id": "billing_export",
    "baseline_remaining_credit": "1000.00",
    "currency": "USD",
    "baseline_at": "2026-10-05T03:00:00Z"
  }
}
```

## 추정 잔액 조회

- Method: `GET`
- Internal path: `/v1/admin/provider-accounts/{account_id}/gcp-credit`
- Gateway path: `/api/v1/admin/provider-accounts/{account_id}/gcp-credit`
- Request body: 없음
- Success: `200 OK`

계산식은 다음과 같다.

```text
estimated_remaining_credit = max(
  baseline_remaining_credit
  - baseline_at 이후 PROMOTION credit 적용액,
  0
)
```

BigQuery의 `credits.amount`는 일반적으로 음수이므로 서버는 합계의 부호를 반대로 바꿔 사용액으로 반환한다.

### 200 response

```json
{
  "account_id": "00000000-0000-4000-8000-000000000001",
  "currency": "USD",
  "estimated_remaining_credit": "987.50",
  "baseline_remaining_credit": "1000.00",
  "promotional_credit_used": "12.50",
  "baseline_at": "2026-10-05T03:00:00Z",
  "data_through": "2026-10-05T03:40:00Z",
  "calculated_at": "2026-10-05T04:00:00Z",
  "is_estimate": true
}
```

`data_through`는 조회 조건에 해당하는 export row의 최신 `export_time`이다. 아직 해당 row가 없으면 `null`일 수 있다. Billing Export 반영 지연, 만료·조정·취소된 크레딧, 기준 시각 직전후의 지연 수집 때문에 Google Credits 화면과 차이가 날 수 있다.

## 오류 응답

오류 body의 공통 형태는 다음과 같다.

```json
{
  "error": {
    "code": "GCP_BILLING_PERMISSION_DENIED",
    "message": "The broker service account cannot query the billing export."
  }
}
```

| HTTP | 적용 API | 대표 code | 의미 |
| --- | --- | --- | --- |
| `400 Bad Request` | PUT, GET | `GCP_ACCOUNT_REQUIRED`, `INVALID_GCP_CREDIT_TRACKING_CONFIG` | 대상이 GCP 계정이 아니거나 저장 설정이 유효하지 않음 |
| `401 Unauthorized` | PUT, GET | Admin 인증 오류 | Admin Bearer token이 없거나 유효하지 않음 |
| `404 Not Found` | PUT, GET | `PROVIDER_ACCOUNT_NOT_FOUND` | Broker 계정 UUID가 존재하지 않음 |
| `409 Conflict` | GET | `GCP_CREDIT_TRACKING_NOT_CONFIGURED` | 아직 기준값을 저장하지 않음 |
| `422 Unprocessable Content` | PUT | FastAPI validation detail | 요청 필드 형식이나 범위가 schema와 맞지 않음 |
| `500 Internal Server Error` | PUT, GET | `DATABASE_ERROR` | Broker DB 저장 또는 조회 실패 |
| `502 Bad Gateway` | GET | `GCP_BILLING_AUTHENTICATION_FAILED`, `GCP_BILLING_PERMISSION_DENIED`, `GCP_BILLING_EXPORT_NOT_FOUND`, `GCP_BILLING_QUERY_INVALID`, `GCP_BILLING_QUERY_FAILED` | 인증, IAM, table 또는 BigQuery query 문제 |
| `503 Service Unavailable` | PUT, GET | `ADMIN_AUTH_NOT_CONFIGURED` | Broker Admin 인증이 설정되지 않음 |
| `503 Service Unavailable` | GET | `GCP_BIGQUERY_SDK_NOT_INSTALLED`, `GCP_BILLING_API_UNAVAILABLE` | Broker 의존성 또는 Google API가 일시적으로 사용 불가 |

응답에는 원본 Google 예외, SQL, credential, token을 포함하지 않는다. 운영 로그에도 Service Account credential과 Admin token을 기록하지 않아야 한다.

## 운영 설정 순서

1. 대상 Billing Account에서 Billing export의 Standard usage cost를 BigQuery dataset으로 연결한다.
2. export table이 생성되고 최신 데이터가 들어오는지 확인한다.
3. Google Cloud Console의 Billing Credits 화면에서 현재 `Remaining value`와 통화를 확인한다.
4. 계정 관리의 GCP 행에서 `비용 조회`를 누르고 Billing Account ID, export project·dataset, 현재 잔액과 통화를 저장한다.
5. 화면에서 기준 잔액, 기준 이후 프로모션 사용액, export 반영 시각을 함께 확인한다.

잔액이 크게 다르면 Google Credits 화면의 최신 값으로 기준을 다시 저장한다. 재저장 전 사용액은 새 계산에 포함되지 않는다.
