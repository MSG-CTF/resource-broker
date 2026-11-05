# GCP 크레딧 마지막 관측값 API

Chrome 확장 기능이 Google Cloud Credits 화면에서 읽은 잔액 표시 문자열을 결제 계정별로 한 건씩 저장한다. 관측 시각은 Broker가 저장할 때 UTC로 기록한다. 이 값은 Google API로 검증한 잔액이나 BigQuery 기반 추정치가 아니다. 계정 관리 화면은 저장값을 먼저 불러오고, 확장 기능으로 새 값을 읽으면 갱신한다. 읽기 실패는 마지막 저장값을 지우지 않는다.

두 API 모두 Admin Bearer 인증을 요구한다. Gateway 경로는 아래 경로 앞에 `/api`를 붙인다.

## 전체 마지막 관측값 조회

- Method: `GET`
- Path: `/v1/admin/gcp-credit-observations`
- Request headers: `Authorization: Bearer <ADMIN_TOKEN>`, `Accept: application/json`
- Request body: 없음
- Success: `200 OK`

```json
{
  "items": [
    {
      "billing_account_id": "000000-000000-000000",
      "amount": "₩1,000",
      "observed_at": "2026-11-04T03:00:00Z"
    }
  ]
}
```

## 마지막 관측값 저장

- Method: `PUT`
- Path: `/v1/admin/gcp-credit-observations/{billing_account_id}`
- Request headers: `Authorization: Bearer <ADMIN_TOKEN>`, `Accept: application/json`, `Content-Type: application/json`
- Request body: `{"amount":"₩1,000"}`. 1~32자의 출력 가능한 문자열이며 ASCII 숫자를 하나 이상 포함해야 한다. `billing_account_id`는 대문자 영숫자 `XXXXXX-XXXXXX-XXXXXX` 형식이다.
- Success: `200 OK`. 응답은 위 항목과 같은 `billing_account_id`, `amount`, 서버가 기록한 `observed_at`을 반환한다. 같은 결제 계정에 다시 저장하면 이전 값과 시각을 교체한다.

## 오류

- `401`: Admin 토큰 누락·만료·오류. `ADMIN_TOKEN_REQUIRED` 또는 `INVALID_ADMIN_TOKEN`.
- `422`: 경로 ID, 본문 값 또는 JSON 형식이 유효하지 않음. FastAPI validation 응답.
- `500`: DB 읽기·저장 실패. `DATABASE_ERROR`.
- `503`: Admin 인증 설정 누락. `ADMIN_AUTH_NOT_CONFIGURED`.
- `400`, `404`, `409`, `502`: 이 두 API에는 해당하지 않음.

`401`, `500`, `503` 오류 본문 형식:

```json
{"error":{"code":"DATABASE_ERROR","message":"The GCP credit observation could not be saved."}}
```
