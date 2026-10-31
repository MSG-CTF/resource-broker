const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";
const ADMIN_TOKEN_STORAGE_KEY = "msg-broker-admin-token";

export class ApiError extends Error {
  constructor(status, payload) {
    const message =
      payload?.error?.message ||
      payload?.detail?.[0]?.msg ||
      `요청을 처리하지 못했습니다. (${status})`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export function getAdminToken() {
  return sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY);
}

export function setAdminToken(token) {
  sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, token);
}

export function clearAdminToken() {
  sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
}

async function request(path, options = {}) {
  const {
    authenticated = true,
    headers: additionalHeaders = {},
    ...requestOptions
  } = options;
  const token = authenticated ? getAdminToken() : null;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...requestOptions,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...additionalHeaders,
    },
  });

  const isJson = response.headers
    .get("content-type")
    ?.includes("application/json");
  const payload = isJson ? await response.json() : null;

  if (!response.ok) {
    if (authenticated && response.status === 401) {
      clearAdminToken();
      window.dispatchEvent(new Event("admin-auth-expired"));
    }
    throw new ApiError(response.status, payload);
  }

  return payload;
}

export function loginAdmin(adminId, password) {
  return request("/v1/admin/auth/login", {
    method: "POST",
    authenticated: false,
    body: JSON.stringify({
      admin_id: adminId,
      password,
    }),
  });
}

export function listProviderAccounts() {
  return request("/v1/admin/provider-accounts");
}

export function listWindowsWorkerPools({ signal } = {}) {
  return request("/v1/admin/windows-worker-pools", { signal });
}

export function createWindowsWorkerPool(payload) {
  return request("/v1/admin/windows-worker-pools", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function listResourceTargets({ provider = "", accountId = "", signal } = {}) {
  const query = new URLSearchParams();
  if (provider) query.set("provider", provider);
  if (accountId) query.set("account_id", accountId);
  query.set("limit", "500");
  return request(`/v1/admin/resource-targets?${query.toString()}`, { signal });
}

export function updateResourceTargetCandidateRegistration(
  resourceTargetId,
  enabled,
) {
  return request(
    `/v1/admin/resource-targets/${encodeURIComponent(resourceTargetId)}`,
    {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    },
  );
}

export function setVmPower(resourceTargetId, action) {
  return request(`/v1/admin/resource-targets/${encodeURIComponent(resourceTargetId)}/power`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
}

export function getVmPowerOperation(resourceTargetId, operationName, action) {
  const query = new URLSearchParams({ action });
  return request(`/v1/admin/resource-targets/${encodeURIComponent(resourceTargetId)}/power-operations/${encodeURIComponent(operationName)}?${query}`);
}

export function listResourceTargetContainers(resourceTargetId, { signal } = {}) {
  return request(
    `/v1/admin/resource-targets/${encodeURIComponent(resourceTargetId)}/containers`,
    { signal },
  );
}

export function listBootstrapJobs(resourceTargetId, { signal } = {}) {
  return request(
    `/v1/admin/resource-targets/${encodeURIComponent(resourceTargetId)}/bootstrap-jobs`,
    { signal },
  );
}

export function createBootstrapJob(resourceTargetId, payload) {
  return request(
    `/v1/admin/resource-targets/${encodeURIComponent(resourceTargetId)}/bootstrap-jobs`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function createProviderAccount(payload) {
  return request("/v1/admin/provider-accounts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function connectAzureUserLogin(accountId, accessToken, expiresOn, { signal } = {}) {
  return request(`/v1/admin/provider-accounts/${encodeURIComponent(accountId)}/azure-login`, {
    method: "POST",
    headers: { "X-Azure-Access-Token": accessToken },
    body: JSON.stringify({ expires_on: expiresOn }),
    signal,
  });
}

export function getAzureUserLogin(accountId, { signal } = {}) {
  return request(`/v1/admin/provider-accounts/${encodeURIComponent(accountId)}/azure-login`, { signal });
}

export function configureAzureManagedIdentity(accountId, payload) {
  return request(`/v1/admin/provider-accounts/${encodeURIComponent(accountId)}/azure-managed-identity`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function verifyProviderAccount(accountId) {
  return request(`/v1/admin/provider-accounts/${accountId}/verify`, {
    method: "POST",
  });
}

export function syncProviderAccount(accountId) {
  return request(`/v1/admin/provider-accounts/${accountId}/sync`, {
    method: "POST",
  });
}

export function updateGcpCreditSettings(accountId, payload) {
  return request(
    `/v1/admin/provider-accounts/${encodeURIComponent(accountId)}/gcp-credit-settings`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export function getGcpCreditEstimate(accountId) {
  return request(
    `/v1/admin/provider-accounts/${encodeURIComponent(accountId)}/gcp-credit`,
  );
}

export function deleteProviderAccount(accountId) {
  return request(`/v1/admin/provider-accounts/${accountId}`, {
    method: "DELETE",
  });
}
