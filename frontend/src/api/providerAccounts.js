const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

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

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
    ...options,
  });

  const isJson = response.headers
    .get("content-type")
    ?.includes("application/json");
  const payload = isJson ? await response.json() : null;

  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }

  return payload;
}

export function listProviderAccounts() {
  return request("/v1/admin/provider-accounts");
}

export function createProviderAccount(payload) {
  return request("/v1/admin/provider-accounts", {
    method: "POST",
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
