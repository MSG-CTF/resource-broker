import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  clearAdminToken,
  createBootstrapJob,
  createProviderAccount,
  deleteProviderAccount,
  getAdminToken,
  listBootstrapJobs,
  listProviderAccounts,
  listResourceTargetContainers,
  listResourceTargets,
  loginAdmin,
  setAdminToken,
  syncProviderAccount,
  updateResourceTargetCandidateRegistration,
  verifyProviderAccount,
} from "./api/providerAccounts.js";

const DEFAULT_K3S_VERSION = "v1.33.3+k3s1";
const CANDIDATE_OBSERVATION_STALE_MS = 10 * 60 * 1000;

const PROVIDER_DEFINITIONS = {
  GCP: {
    label: "Google Cloud",
    shortLabel: "GCP",
    avatar: "G",
    accountLabel: "외부 관리 계정 ID",
    accountPlaceholder: "예: team-alpha 또는 owner@example.com",
    accountType: "text",
    accountHelp:
      "프로젝트 묶음의 소유자나 관리 단위를 구분하는 ID입니다. GCP 인증에는 사용하지 않습니다.",
    configFields: [
      {
        name: "projectIds",
        configKey: "project_ids",
        label: "GCP 프로젝트 ID",
        placeholder: "project-alpha\nproject-beta",
        help: "여러 개라면 줄바꿈 또는 쉼표로 구분하세요.",
        kind: "list",
      },
    ],
  },
  AWS: {
    label: "Amazon Web Services",
    shortLabel: "AWS",
    avatar: "A",
    accountLabel: "AWS Account ID",
    accountPlaceholder: "123456789012",
    accountType: "text",
    accountHelp: "AWS 계정을 식별하는 12자리 Account ID입니다.",
    configFields: [
      {
        name: "roleArn",
        configKey: "role_arn",
        label: "Inventory Role ARN",
        placeholder: "arn:aws:iam::123456789012:role/MsgBrokerInventoryRole",
        help: "Hub Role이 AssumeRole할 대상 계정의 EC2 조회 전용 IAM Role입니다.",
        kind: "text",
      },
      {
        name: "bootstrapRoleArn",
        configKey: "bootstrap_role_arn",
        label: "Bootstrap Role ARN (선택)",
        placeholder: "arn:aws:iam::123456789012:role/MsgBrokerBootstrapRole",
        help: "비우면 계정 ID의 MsgBrokerBootstrapRole ARN을 자동으로 사용합니다.",
        kind: "text",
        required: false,
      },
      {
        name: "regions",
        configKey: "regions",
        label: "AWS Regions",
        placeholder: "ap-northeast-2\nus-east-1",
        help: "EC2를 조회할 Region을 입력하세요.",
        kind: "list",
      },
    ],
  },
  AZURE: {
    label: "Microsoft Azure",
    shortLabel: "Azure",
    avatar: "Az",
    accountLabel: "Tenant ID",
    accountPlaceholder: "00000000-0000-0000-0000-000000000000",
    accountType: "text",
    accountHelp: "Azure 계정을 대표하는 Microsoft Entra Tenant ID입니다.",
    configFields: [
      {
        name: "clientId",
        configKey: "client_id",
        label: "Client ID",
        placeholder: "00000000-0000-0000-0000-000000000000",
        help: "브로커 워크로드와 연결할 애플리케이션 Client ID입니다.",
        kind: "text",
      },
      {
        name: "subscriptionIds",
        configKey: "subscription_ids",
        label: "Subscription IDs",
        placeholder: "subscription-id-1\nsubscription-id-2",
        help: "VM을 조회할 Subscription ID를 입력하세요.",
        kind: "list",
      },
    ],
  },
};

const PROVIDER_ORDER = ["GCP", "AWS", "AZURE"];
const ACTIVE_PROVIDERS = new Set(["GCP", "AWS", "AZURE"]);

const STATUS_LABELS = {
  VALID: "인증 정상",
  INVALID: "인증 실패",
  SUFFICIENT: "권한 충분",
  INSUFFICIENT: "권한 부족",
  AVAILABLE: "API 정상",
  UNAVAILABLE: "API 장애",
  UNKNOWN: "확인 전",
};

function createEmptyForm(provider = "GCP") {
  const configValues = Object.fromEntries(
    PROVIDER_DEFINITIONS[provider].configFields.map((field) => [field.name, ""]),
  );
  return {
    provider,
    externalAccountId: "",
    displayName: "",
    enabled: true,
    configValues,
  };
}

function splitList(value) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildProviderConfig(provider, configValues) {
  const entries = PROVIDER_DEFINITIONS[provider].configFields.flatMap((field) => {
    const value = field.kind === "list"
      ? splitList(configValues[field.name] || "")
      : (configValues[field.name] || "").trim();
    if (field.required === false && !value) return [];
    return [[field.configKey, value]];
  });
  return Object.fromEntries(entries);
}

function accountScopes(account) {
  const config = account.config || {};
  switch (account.provider) {
    case "GCP":
      return config.project_ids || [];
    case "AWS":
      return config.regions || [];
    case "AZURE":
      return config.subscription_ids || [];
    default:
      return [];
  }
}

function formatDate(value) {
  if (!value) return "아직 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatCpu(value) {
  if (value === null || value === undefined) return "미수집";
  if (value === 0) return "0m";
  if (value % 1000 === 0) return `${value / 1000} vCPU`;
  return `${value}m`;
}

function formatMib(value) {
  if (value === null || value === undefined) return "미수집";
  return `${new Intl.NumberFormat("en-US").format(value)} MiB`;
}

function CapacitySummary({
  capacity,
  label = "사양",
  variant = "default",
  description,
}) {
  return (
    <section
      className={`capacity-summary capacity-${variant}`}
      title={description}
    >
      <span className="capacity-label">{label}</span>
      <div className="capacity-metrics">
        <div>
          <span>CPU</span>
          <strong>{formatCpu(capacity?.cpu_millicores)}</strong>
        </div>
        <div>
          <span>Memory</span>
          <strong>{formatMib(capacity?.memory_mib)}</strong>
        </div>
        <div>
          <span>Storage</span>
          <strong>{formatMib(capacity?.storage_mib)}</strong>
        </div>
      </div>
    </section>
  );
}

function UsageSummary({ usage }) {
  return (
    <section
      className="capacity-summary capacity-usage"
      title="Node Agent가 관측한 OS, k3s, Pod를 포함한 VM 전체의 실제 CPU·메모리 사용량입니다."
    >
      <span className="capacity-label">VM 전체 실제 사용량</span>
      <div className="usage-metrics">
        <div>
          <span>CPU</span>
          <strong>{formatCpu(usage?.cpu_millicores)}</strong>
        </div>
        <div>
          <span>Memory</span>
          <strong>{formatMib(usage?.memory_mib)}</strong>
        </div>
      </div>
    </section>
  );
}

const CANDIDATE_REASON_LABELS = {
  PROVIDER_ACCOUNT_DISABLED: "Provider 계정이 사용 중지됨",
  RESOURCE_TARGET_RETIRED: "동기화 대상에서 제외된 VM",
  PROVIDER_INSTANCE_NOT_RUNNING: "Cloud 동기화에서 실행 중인 VM으로 확인되지 않음",
  RUNTIME_NOT_READY: "Runtime 준비 안 됨",
  ARCHITECTURE_NOT_OBSERVED: "아키텍처 미수집",
  RUNTIME_TARGET_NOT_OBSERVED: "Runtime target 미수집",
  RUNTIME_OBSERVATION_MISSING: "Agent 관측 없음",
  RUNTIME_OBSERVATION_STALE: "Agent 관측 만료 또는 시각 오류",
  PROVIDER_CAPACITY_NOT_OBSERVED: "Provider CPU·메모리 미수집",
  RUNTIME_USAGE_NOT_OBSERVED: "VM 실제 사용량 미수집",
  ALLOCATABLE_CAPACITY_NOT_OBSERVED: "배치 가능량 미수집",
};

function candidateRegistrationReasons(resource) {
  const reasons = [];
  if (resource.account_enabled === false) {
    reasons.push("PROVIDER_ACCOUNT_DISABLED");
  }
  if (resource.retired_at) reasons.push("RESOURCE_TARGET_RETIRED");
  if (resource.status !== "RUNNING") {
    reasons.push("PROVIDER_INSTANCE_NOT_RUNNING");
  }
  if (!resource.runtime?.ready) reasons.push("RUNTIME_NOT_READY");
  if (!resource.architecture) reasons.push("ARCHITECTURE_NOT_OBSERVED");
  if (!resource.runtime?.type || !resource.runtime?.target_id) {
    reasons.push("RUNTIME_TARGET_NOT_OBSERVED");
  }

  const lastSeenAt = resource.runtime?.last_seen_at;
  if (!resource.runtime?.observed_at || !lastSeenAt) {
    reasons.push("RUNTIME_OBSERVATION_MISSING");
  } else {
    const receivedAt = Date.parse(lastSeenAt);
    const observedAt = Date.parse(resource.runtime.observed_at);
    const now = Date.now();
    if (
      Number.isNaN(receivedAt) ||
      Number.isNaN(observedAt) ||
      now - receivedAt >= CANDIDATE_OBSERVATION_STALE_MS ||
      now - observedAt >= CANDIDATE_OBSERVATION_STALE_MS ||
      observedAt > now + 30_000
    ) {
      reasons.push("RUNTIME_OBSERVATION_STALE");
    }
  }

  if (
    resource.provider_capacity?.cpu_millicores == null ||
    resource.provider_capacity?.memory_mib == null
  ) {
    reasons.push("PROVIDER_CAPACITY_NOT_OBSERVED");
  }
  if (
    resource.runtime?.usage?.cpu_millicores == null ||
    resource.runtime?.usage?.memory_mib == null
  ) {
    reasons.push("RUNTIME_USAGE_NOT_OBSERVED");
  }
  if (
    resource.allocatable_capacity?.cpu_millicores == null ||
    resource.allocatable_capacity?.memory_mib == null ||
    resource.allocatable_capacity?.storage_mib == null
  ) {
    reasons.push("ALLOCATABLE_CAPACITY_NOT_OBSERVED");
  }
  return reasons;
}

function CandidateRegistrationControl({
  resource,
  submitting,
  onChange,
}) {
  const reasons = candidateRegistrationReasons(resource);
  const eligible = reasons.length === 0;
  const registered = resource.enabled;
  const label = registered
    ? eligible
      ? "후보 등록됨"
      : "등록됨 · 현재 사용 불가"
    : eligible
      ? "후보 등록 가능"
      : "후보 등록 불가";
  const reasonText = reasons
    .map((reason) => CANDIDATE_REASON_LABELS[reason] || reason)
    .join(" · ");

  return (
    <div className="candidate-registration-control">
      <StatusBadge
        value={registered ? (eligible ? "AVAILABLE" : "UNKNOWN") : "TERMINATED"}
        label={label}
      />
      {reasonText && (
        <span className="candidate-registration-reason" title={reasonText}>
          {reasonText}
        </span>
      )}
      <button
        className={`button ${registered ? "button-ghost" : "button-primary"}`}
        type="button"
        disabled={submitting || (!registered && !eligible)}
        title={!registered && !eligible ? reasonText : undefined}
        onClick={(event) => {
          event.stopPropagation();
          onChange(resource, !registered);
        }}
      >
        {submitting
          ? "처리 중..."
          : registered
            ? "후보 등록 해제"
            : "후보 등록"}
      </button>
    </div>
  );
}

function statusTone(value) {
  if (
    ["VALID", "SUFFICIENT", "AVAILABLE", "RUNNING"].includes(
      String(value).toUpperCase(),
    )
  ) {
    return "positive";
  }
  if (["INVALID", "INSUFFICIENT", "UNAVAILABLE"].includes(value)) {
    return "negative";
  }
  if (value === "TERMINATED") return "neutral";
  return "pending";
}

function StatusBadge({ value, label }) {
  return (
    <span className={`status-badge status-${statusTone(value)}`}>
      <span className="status-dot" aria-hidden="true" />
      {label || STATUS_LABELS[value] || value}
    </span>
  );
}

function ErrorBanner({ error, onClose }) {
  if (!error) return null;
  const fieldErrors = error instanceof ApiError ? error.payload?.detail : null;

  return (
    <div className="notice notice-error" role="alert">
      <div>
        <strong>{error.status ? `요청 실패 (${error.status})` : "연결 실패"}</strong>
        <p>{error.message}</p>
        {Array.isArray(fieldErrors) && fieldErrors.length > 0 && (
          <ul>
            {fieldErrors.map((item, index) => (
              <li key={`${item.loc?.join(".")}-${index}`}>{item.msg}</li>
            ))}
          </ul>
        )}
      </div>
      <button type="button" onClick={onClose} aria-label="오류 메시지 닫기">
        닫기
      </button>
    </div>
  );
}

function LoginScreen({ onAuthenticated }) {
  const [adminId, setAdminId] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      const response = await loginAdmin(adminId.trim(), password);
      setAdminToken(response.access_token);
      setPassword("");
      onAuthenticated();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-shell">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">
          <div className="brand-mark">MB</div>
          <div>
            <strong>MSG Broker</strong>
            <span>Administrator console</span>
          </div>
        </div>

        <div className="login-heading">
          <span className="eyebrow">Restricted access</span>
          <h1 id="login-title">관리자 로그인</h1>
          <p>클라우드 계정과 VM 정보를 관리하려면 인증이 필요합니다.</p>
        </div>

        <ErrorBanner error={error} onClose={() => setError(null)} />

        <form className="login-form" onSubmit={handleSubmit}>
          <label>
            <span>관리자 ID</span>
            <input
              type="text"
              value={adminId}
              onChange={(event) => setAdminId(event.target.value)}
              autoComplete="username"
              required
              autoFocus
            />
          </label>
          <label>
            <span>비밀번호</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          <button
            className="button button-primary button-block"
            disabled={submitting}
          >
            {submitting ? "로그인 중..." : "로그인"}
          </button>
        </form>
      </section>
    </div>
  );
}

function RegisterForm({ onCreated }) {
  const [form, setForm] = useState(createEmptyForm());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const definition = PROVIDER_DEFINITIONS[form.provider];

  function updateField(event) {
    const { name, value, checked, type } = event.target;
    setForm((current) => ({
      ...current,
      [name]: type === "checkbox" ? checked : value,
    }));
  }

  function updateConfigField(event) {
    const { name, value } = event.target;
    setForm((current) => ({
      ...current,
      configValues: {
        ...current.configValues,
        [name]: value,
      },
    }));
  }

  function updateProvider(event) {
    const provider = event.target.value;
    setForm((current) => ({
      ...createEmptyForm(provider),
      displayName: current.displayName,
      enabled: current.enabled,
    }));
    setError(null);
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    const externalAccountId = form.externalAccountId.trim();

    try {
      const account = await createProviderAccount({
        provider: form.provider,
        external_account_id: externalAccountId,
        display_name: form.displayName.trim() || null,
        enabled: form.enabled,
        config: buildProviderConfig(form.provider, form.configValues),
      });
      setForm(createEmptyForm(form.provider));
      onCreated(account);
    } catch (requestError) {
      setError(requestError);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="panel register-panel" aria-labelledby="register-title">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Multi-cloud onboarding</span>
          <h2 id="register-title">클라우드 계정 등록</h2>
        </div>
        <span className="provider-chip">{definition.shortLabel}</span>
      </div>

      <ErrorBanner error={error} onClose={() => setError(null)} />

      <form onSubmit={handleSubmit} className="register-form">
        <label>
          <span>Provider</span>
          <select name="provider" value={form.provider} onChange={updateProvider}>
            {PROVIDER_ORDER.map((provider) => (
              <option key={provider} value={provider}>
                {PROVIDER_DEFINITIONS[provider].label}
              </option>
            ))}
          </select>
          <small>
            {ACTIVE_PROVIDERS.has(form.provider)
              ? "현재 실제 Verify와 Sync까지 지원합니다."
              : "현재는 계정 등록과 목록 확인만 지원합니다."}
          </small>
        </label>

        <label>
          <span>{definition.accountLabel}</span>
          <input
            type={definition.accountType}
            name="externalAccountId"
            value={form.externalAccountId}
            onChange={updateField}
            placeholder={definition.accountPlaceholder}
            required
          />
          <small>{definition.accountHelp}</small>
        </label>

        <label>
          <span>표시 이름</span>
          <input
            type="text"
            name="displayName"
            value={form.displayName}
            onChange={updateField}
            placeholder={`예: 운영 ${definition.shortLabel} 계정`}
          />
          <small>관리 화면에서 계정을 구분할 때 사용합니다.</small>
        </label>

        <div className="config-divider">
          <span>{definition.shortLabel} 설정</span>
          <small>provider_config에 저장되는 Provider 전용 필드입니다.</small>
        </div>

        {definition.configFields.map((field) => (
          <label key={field.name}>
            <span>{field.label}</span>
            {field.kind === "list" ? (
              <textarea
                name={field.name}
                value={form.configValues[field.name] || ""}
                onChange={updateConfigField}
                placeholder={field.placeholder}
                rows="3"
                required={field.required !== false}
              />
            ) : (
              <input
                type="text"
                name={field.name}
                value={form.configValues[field.name] || ""}
                onChange={updateConfigField}
                placeholder={field.placeholder}
                required={field.required !== false}
              />
            )}
            <small>{field.help}</small>
          </label>
        ))}

        <label className="toggle-row">
          <span>
            <strong>계정 사용</strong>
            <small>추후 후보 리소스로 사용할 수 있게 등록합니다.</small>
          </span>
          <input
            type="checkbox"
            name="enabled"
            checked={form.enabled}
            onChange={updateField}
          />
        </label>

        <button className="button button-primary button-block" disabled={submitting}>
          {submitting ? "등록 중..." : "계정 등록하기"}
        </button>
      </form>
    </section>
  );
}

function AccountTable({
  accounts,
  actionState,
  onVerify,
  onSync,
  onDelete,
}) {
  if (accounts.length === 0) {
    return (
      <div className="empty-state">
        <span>0</span>
        <h3>등록된 계정이 없습니다</h3>
        <p>왼쪽 양식에서 첫 클라우드 계정을 등록해 보세요.</p>
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>계정</th>
            <th>관리 범위</th>
            <th>상태</th>
            <th>최근 확인</th>
            <th className="align-right">작업</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => {
            const definition =
              PROVIDER_DEFINITIONS[account.provider] || PROVIDER_DEFINITIONS.GCP;
            const isImplemented = ACTIVE_PROVIDERS.has(account.provider);
            const isVerifying = actionState === `verify:${account.account_id}`;
            const isSyncing = actionState === `sync:${account.account_id}`;
            const isDeleting = actionState === `delete:${account.account_id}`;
            const scopes = accountScopes(account);

            return (
              <tr key={account.account_id}>
                <td>
                  <div className="account-cell">
                    <div className="provider-avatar">{definition.avatar}</div>
                    <div>
                      <strong>
                        {account.display_name || `이름 없는 ${definition.shortLabel} 계정`}
                      </strong>
                      <span>{account.external_account_id}</span>
                      <span className="provider-name">{definition.label}</span>
                      {account.provider === "AWS" && (
                        <details className="aws-onboarding-values">
                          <summary>AWS 연결 값</summary>
                          <span>External ID</span>
                          <code>{account.config?.external_id}</code>
                          <span>Bootstrap Role</span>
                          <code>{account.config?.bootstrap_role_arn}</code>
                        </details>
                      )}
                      {!account.enabled && <em>사용 중지</em>}
                    </div>
                  </div>
                </td>
                <td>
                  <div className="project-list">
                    {scopes.slice(0, 3).map((scope) => (
                      <code key={scope}>{scope}</code>
                    ))}
                    {scopes.length > 3 && <span>외 {scopes.length - 3}개</span>}
                  </div>
                </td>
                <td>
                  <div className="status-stack">
                    <StatusBadge value={account.credential_status} />
                    <StatusBadge value={account.permission_status} />
                    <StatusBadge value={account.provider_api_status} />
                  </div>
                </td>
                <td>
                  <div className="date-cell">
                    <span>검증 {formatDate(account.last_verified_at)}</span>
                    <span>동기화 {formatDate(account.last_synced_at)}</span>
                  </div>
                </td>
                <td>
                  <div className="row-actions">
                    <button
                      className="button button-secondary"
                      type="button"
                      disabled={!isImplemented || Boolean(actionState)}
                      title={isImplemented ? "인증과 권한 검증" : "어댑터 준비 중"}
                      onClick={() => onVerify(account)}
                    >
                      {isVerifying ? "검증 중..." : "Verify"}
                    </button>
                    <button
                      className="button button-primary"
                      type="button"
                      disabled={!isImplemented || Boolean(actionState)}
                      title={isImplemented ? "VM 목록 동기화" : "어댑터 준비 중"}
                      onClick={() => onSync(account)}
                    >
                      {isSyncing ? "동기화 중..." : "Sync"}
                    </button>
                    <button
                      className="button button-danger"
                      type="button"
                      disabled={Boolean(actionState)}
                      title="Broker DB에서 계정과 VM snapshot 삭제"
                      onClick={() => onDelete(account)}
                    >
                      {isDeleting ? "삭제 중..." : "삭제"}
                    </button>
                    {!isImplemented && <small className="adapter-pending">준비 중</small>}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SyncResult({ result, accountName }) {
  if (!result) return null;

  return (
    <section className="panel sync-result-panel" aria-labelledby="resources-title">
      <div className="panel-heading resources-heading">
        <div>
          <span className="eyebrow">Latest cloud snapshot</span>
          <h2 id="resources-title">{accountName} VM 동기화 결과</h2>
          <p>{formatDate(result.synced_at)} 기준</p>
        </div>
        <StatusBadge
          value={result.success ? "AVAILABLE" : "UNAVAILABLE"}
          label={result.success ? "동기화 완료" : "동기화 실패"}
        />
      </div>

      <div className="sync-metrics">
        <div><span>발견</span><strong>{result.discovered_count}</strong></div>
        <div><span>신규</span><strong>{result.created_count}</strong></div>
        <div><span>갱신</span><strong>{result.updated_count}</strong></div>
        <div><span>Retired</span><strong>{result.retired_count}</strong></div>
      </div>

      {result.scopes?.some((scope) => !scope.success) && (
        <div className="scope-warning">
          {result.scopes
            .filter((scope) => !scope.success)
            .map((scope) => (
              <span key={scope.scope_id}>
                {scope.scope_id} · {scope.message || scope.error_code}
              </span>
            ))}
        </div>
      )}
    </section>
  );
}

function ResourceInventory({
  resources,
  loading,
  provider,
  actionState,
  onProviderChange,
  onCandidateRegistrationChange,
  onRefresh,
}) {
  const [selectedResource, setSelectedResource] = useState(null);
  const [containers, setContainers] = useState([]);
  const [containersLoading, setContainersLoading] = useState(false);
  const [containersError, setContainersError] = useState(null);
  const [containersObservedAt, setContainersObservedAt] = useState(null);
  const [bootstrapJobs, setBootstrapJobs] = useState([]);
  const [bootstrapLoading, setBootstrapLoading] = useState(false);
  const [bootstrapError, setBootstrapError] = useState(null);
  const [bootstrapAction, setBootstrapAction] = useState("INSTALL");
  const [bootstrapVersion, setBootstrapVersion] = useState("0.4.1");
  const [k3sVersion, setK3sVersion] = useState(DEFAULT_K3S_VERSION);
  const [agentImage, setAgentImage] = useState("");
  const [bootstrapSubmitting, setBootstrapSubmitting] = useState(false);
  const [bootstrapReloadKey, setBootstrapReloadKey] = useState(0);
  const [registrationFilter, setRegistrationFilter] = useState("");

  useEffect(() => {
    if (!selectedResource) return undefined;

    function handleEscape(event) {
      if (event.key === "Escape") {
        setSelectedResource(null);
      }
    }

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [selectedResource]);

  useEffect(() => {
    if (!selectedResource) {
      setBootstrapJobs([]);
      setBootstrapError(null);
      setBootstrapLoading(false);
      return undefined;
    }

    const controller = new AbortController();
    let timer;
    async function loadJobs() {
      try {
        const response = await listBootstrapJobs(
          selectedResource.resource_target_id,
          { signal: controller.signal },
        );
        setBootstrapJobs(response.items);
        setBootstrapError(null);
        const active = response.items.some((job) =>
          ["QUEUED", "APPLYING", "RUNNING"].includes(job.status),
        );
        if (active && !controller.signal.aborted) {
          timer = window.setTimeout(loadJobs, 5000);
        }
      } catch (requestError) {
        if (requestError.name !== "AbortError") {
          setBootstrapError(requestError);
        }
      } finally {
        if (!controller.signal.aborted) setBootstrapLoading(false);
      }
    }
    setBootstrapLoading(true);
    loadJobs();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [selectedResource, bootstrapReloadKey]);

  async function handleBootstrapSubmit(event) {
    event.preventDefault();
    if (!selectedResource || bootstrapSubmitting) return;
    setBootstrapSubmitting(true);
    setBootstrapError(null);
    try {
      const needsVersions = ["INSTALL", "UPDATE"].includes(bootstrapAction);
      const created = await createBootstrapJob(
        selectedResource.resource_target_id,
        {
          action: bootstrapAction,
          bootstrap_version: bootstrapVersion,
          k3s_version: needsVersions ? k3sVersion : null,
          agent_image: needsVersions ? agentImage : null,
        },
      );
      setBootstrapJobs((current) => [created, ...current]);
      setBootstrapReloadKey((current) => current + 1);
    } catch (requestError) {
      setBootstrapError(requestError);
    } finally {
      setBootstrapSubmitting(false);
    }
  }

  useEffect(() => {
    if (!selectedResource) {
      setContainers([]);
      setContainersError(null);
      setContainersObservedAt(null);
      setContainersLoading(false);
      return undefined;
    }

    const controller = new AbortController();
    setContainers([]);
    setContainersError(null);
    setContainersObservedAt(null);
    setContainersLoading(true);

    listResourceTargetContainers(selectedResource.resource_target_id, {
      signal: controller.signal,
    })
      .then((response) => {
        setContainers(response.items);
        setContainersObservedAt(response.observed_at);
      })
      .catch((requestError) => {
        if (requestError.name !== "AbortError") {
          setContainersError(requestError);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setContainersLoading(false);
        }
      });

    return () => controller.abort();
  }, [selectedResource]);

  const selectedDefinition = selectedResource
    ? PROVIDER_DEFINITIONS[selectedResource.provider] ||
      PROVIDER_DEFINITIONS.GCP
    : null;
  const selectedScopeLabel =
    selectedResource?.provider === "AZURE"
      ? "Subscription ID"
      : selectedResource?.provider === "GCP"
        ? "Project ID"
        : selectedResource?.provider === "AWS"
          ? "AWS Region"
          : "Scope ID";
  const visibleResources = useMemo(() => {
    if (registrationFilter === "registered") {
      return resources.filter((resource) => resource.enabled);
    }
    if (registrationFilter === "unregistered") {
      return resources.filter((resource) => !resource.enabled);
    }
    return resources;
  }, [registrationFilter, resources]);

  return (
    <section className="panel resources-panel" aria-labelledby="inventory-title">
      <div className="panel-heading resources-heading">
        <div>
          <span className="eyebrow">Database snapshot</span>
          <h2 id="inventory-title">전체 VM 인벤토리</h2>
          <p>
            Cloud API를 다시 호출하지 않고 Broker DB 값을 표시합니다. Scheduler
            배치 가능량은 Pod requests 기준과 VM 실사용 기준 중 작은 값입니다.
          </p>
        </div>
        <div className="inventory-controls">
          <select
            value={provider}
            onChange={(event) => onProviderChange(event.target.value)}
            aria-label="클라우드 필터"
          >
            <option value="">전체 클라우드</option>
            {PROVIDER_ORDER.map((item) => (
              <option key={item} value={item}>
                {PROVIDER_DEFINITIONS[item].label}
              </option>
            ))}
          </select>
          <select
            value={registrationFilter}
            onChange={(event) => setRegistrationFilter(event.target.value)}
            aria-label="후보 등록 상태 필터"
          >
            <option value="">전체 등록 상태</option>
            <option value="registered">후보 등록 VM</option>
            <option value="unregistered">후보 미등록 VM</option>
          </select>
          <button className="button button-ghost" type="button" onClick={onRefresh}>
            새로고침
          </button>
        </div>
      </div>

      {loading ? (
        <div className="loading-state">VM 목록을 불러오는 중입니다...</div>
      ) : resources.length === 0 ? (
        <div className="empty-inline">
          저장된 VM이 없습니다. 계정을 동기화하면 이곳에 표시됩니다.
        </div>
      ) : visibleResources.length === 0 ? (
        <div className="empty-inline">
          현재 등록 상태 필터에 해당하는 VM이 없습니다.
        </div>
      ) : (
        <div className="vm-list">
          <div className="vm-list-header" aria-hidden="true">
            <span>VM</span>
            <span>환경</span>
            <span>Provider 전체 사양</span>
            <span />
            <span>Scheduler 배치 가능량(예약 전)</span>
            <span>VM 전체 실제 사용량</span>
            <span>네트워크</span>
          </div>
          {visibleResources.map((resource) => {
            const definition =
              PROVIDER_DEFINITIONS[resource.provider] ||
              PROVIDER_DEFINITIONS.GCP;
            return (
              <article
                className="vm-list-row"
                key={resource.resource_target_id}
                role="button"
                tabIndex={0}
                aria-label={`${resource.name || "이름 없는 VM"} 상세 정보 열기`}
                onClick={() => setSelectedResource(resource)}
                onKeyDown={(event) => {
                  if (event.target !== event.currentTarget) return;
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedResource(resource);
                  }
                }}
              >
                <section className="vm-list-identity">
                  <div className="vm-identity">
                    <div className="provider-avatar">{definition.avatar}</div>
                    <div>
                      <span className="vm-provider">{definition.label}</span>
                      <h3>{resource.name || "이름 없는 VM"}</h3>
                      <p>
                        {resource.account_display_name ||
                          resource.external_account_id}
                      </p>
                    </div>
                  </div>
                  <div className="vm-status">
                    <StatusBadge value={resource.status || "UNKNOWN"} />
                    {resource.retired_at && <span>Retired</span>}
                    <CandidateRegistrationControl
                      resource={resource}
                      submitting={
                        actionState ===
                        `candidate:${resource.resource_target_id}`
                      }
                      onChange={onCandidateRegistrationChange}
                    />
                  </div>
                </section>

                <section className="vm-list-environment">
                  <div>
                    <span>Machine type</span>
                    <strong>{resource.machine_type || "미수집"}</strong>
                  </div>
                  <div>
                    <span>Location</span>
                    <strong>
                      {resource.region}
                      {resource.zone ? ` · ${resource.zone}` : ""}
                    </strong>
                  </div>
                </section>

                <div className="vm-list-capacity">
                  <CapacitySummary
                    capacity={resource.provider_capacity}
                    label="Provider 전체 사양"
                    variant="provider"
                  />
                </div>
                <div className="capacity-list-arrow" aria-hidden="true">→</div>
                <div className="vm-list-capacity">
                  <CapacitySummary
                    capacity={resource.allocatable_capacity}
                    label="Scheduler 배치 가능량(예약 전)"
                    variant="allocatable"
                    description="Node allocatable에서 Pod requests를 뺀 값과 Provider 전체 사양에서 VM 실제 사용량을 뺀 값 중 더 작은 값입니다. Broker reservation은 아직 차감하지 않았습니다."
                  />
                </div>
                <div className="vm-list-capacity">
                  <UsageSummary usage={resource.runtime?.usage} />
                </div>

                <section className="vm-list-network">
                  <span>
                    <strong>Private IP</strong>
                    {resource.internal_ip || "없음"}
                  </span>
                  <span>
                    <strong>Public IP</strong>
                    {resource.external_ip || "없음"}
                  </span>
                </section>
              </article>
            );
          })}
        </div>
      )}

      {selectedResource && (
        <div
          className="resource-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setSelectedResource(null);
            }
          }}
        >
          <section
            className="resource-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="resource-modal-title"
          >
            <header className="resource-modal-header">
              <div className="vm-identity">
                <div className="provider-avatar">{selectedDefinition.avatar}</div>
                <div>
                  <span className="vm-provider">{selectedDefinition.label}</span>
                  <h2 id="resource-modal-title">
                    {selectedResource.name || "이름 없는 VM"}
                  </h2>
                </div>
              </div>
              <button
                className="resource-modal-close"
                type="button"
                aria-label="상세 정보 닫기"
                onClick={() => setSelectedResource(null)}
              >
                ×
              </button>
            </header>

            <div className="resource-modal-body">
              <div className="resource-detail-primary">
                <span>Broker Account ID</span>
                <code>{selectedResource.account_id}</code>
              </div>
              <div className="resource-detail-primary">
                <span>{selectedScopeLabel}</span>
                <code>{selectedResource.scope_id || "없음"}</code>
              </div>
              <dl className="resource-detail-grid">
                <div>
                  <dt>계정 표시 이름</dt>
                  <dd>{selectedResource.account_display_name || "없음"}</dd>
                </div>
                <div>
                  <dt>External Account ID</dt>
                  <dd>{selectedResource.external_account_id || "없음"}</dd>
                </div>
                <div>
                  <dt>Resource Target ID</dt>
                  <dd>{selectedResource.resource_target_id}</dd>
                </div>
                <div>
                  <dt>Provider Instance ID</dt>
                  <dd>{selectedResource.instance_id || "없음"}</dd>
                </div>
              </dl>

              <section className="bootstrap-jobs" aria-labelledby="bootstrap-jobs-title">
                <header className="bootstrap-jobs-header">
                  <div>
                    <span className="eyebrow">Automated bootstrap</span>
                    <h3 id="bootstrap-jobs-title">Node Agent 설치 작업</h3>
                    <p>
                      {selectedResource.provider === "GCP"
                        ? "Ubuntu AMD64/ARM64 VM에 임시 작업 라벨과 OS Policy를 만들고 완료 후 자동 제거합니다."
                        : selectedResource.provider === "AWS"
                          ? "SSM online 상태이며 msg-broker-bootstrap=enabled 태그가 있는 Ubuntu AMD64/ARM64 EC2에서 실행됩니다."
                          : selectedResource.provider === "AZURE"
                            ? "Azure VM Agent가 준비되고 msg-broker-bootstrap=enabled 태그가 있는 Ubuntu AMD64/ARM64 VM에서 Run Command로 실행됩니다."
                          : "현재 공통 Bootstrap은 Ubuntu AMD64/ARM64를 지원합니다."}
                    </p>
                  </div>
                  <strong>{bootstrapLoading ? "조회 중" : `${bootstrapJobs.length}건`}</strong>
                </header>

                {["GCP", "AWS", "AZURE"].includes(selectedResource.provider) ? (
                  <form className="bootstrap-job-form" onSubmit={handleBootstrapSubmit}>
                    <label>
                      <span>작업</span>
                      <select value={bootstrapAction} onChange={(event) => setBootstrapAction(event.target.value)}>
                        <option value="INSTALL">설치</option>
                        <option value="UPDATE">업데이트</option>
                        <option value="CHECK">상태 확인</option>
                        <option value="REMOVE">Agent 제거</option>
                      </select>
                    </label>
                    <label>
                      <span>Bootstrap 버전</span>
                      <input required pattern="[0-9]+\.[0-9]+\.[0-9]+" value={bootstrapVersion} onChange={(event) => setBootstrapVersion(event.target.value)} placeholder="0.4.1" />
                    </label>
                    {["INSTALL", "UPDATE"].includes(bootstrapAction) && (
                      <>
                        <label>
                          <span>k3s 버전</span>
                          <input required value={k3sVersion} onChange={(event) => setK3sVersion(event.target.value)} placeholder="v1.33.3+k3s1" />
                        </label>
                        <label className="bootstrap-image-field">
                          <span>Node Agent image (멀티아키텍처 digest 필수)</span>
                          <input required value={agentImage} onChange={(event) => setAgentImage(event.target.value)} placeholder="registry.example/agent@sha256:..." />
                        </label>
                      </>
                    )}
                    <button
                      className="button button-primary"
                      type="submit"
                      disabled={bootstrapSubmitting || bootstrapJobs.some((job) =>
                        ["QUEUED", "APPLYING", "RUNNING"].includes(job.status),
                      )}
                    >
                      {bootstrapSubmitting ? "등록 중..." : "작업 시작"}
                    </button>
                  </form>
                ) : (
                  <div className="bootstrap-job-state">
                    {selectedResource.provider} 실행 adapter는 아직 구현되지 않았습니다.
                  </div>
                )}

                {bootstrapError && (
                  <div className="bootstrap-job-state is-error" role="alert">
                    {bootstrapError.message}
                  </div>
                )}
                {bootstrapJobs.length > 0 && (
                  <div className="bootstrap-job-list">
                    {bootstrapJobs.map((job) => (
                      <article key={job.job_id}>
                        <div><strong>{job.action}</strong><code>{job.job_id}</code></div>
                        <StatusBadge value={job.status} />
                        <span>{job.error_message || formatDate(job.created_at)}</span>
                      </article>
                    ))}
                  </div>
                )}
              </section>

              <section
                className="runtime-containers"
                aria-labelledby="runtime-containers-title"
              >
                <header className="runtime-containers-header">
                  <div>
                    <span className="eyebrow">Runtime snapshot</span>
                    <h3 id="runtime-containers-title">현재 컨테이너</h3>
                    <p>
                      새 워크로드 배치 판단에 사용하는 Kubernetes request입니다.
                    </p>
                  </div>
                  <div className="runtime-containers-summary">
                    <strong>
                      {containersLoading ? "조회 중" : `${containers.length}개`}
                    </strong>
                    <span>{formatDate(containersObservedAt)}</span>
                  </div>
                </header>

                {containersLoading ? (
                  <div className="runtime-containers-state">
                    컨테이너 snapshot을 불러오는 중입니다...
                  </div>
                ) : containersError ? (
                  <div className="runtime-containers-state is-error" role="alert">
                    {containersError.message}
                  </div>
                ) : containers.length === 0 ? (
                  <div className="runtime-containers-state">
                    아직 관측된 컨테이너가 없습니다.
                  </div>
                ) : (
                  <div className="runtime-container-list">
                    {containers.map((container) => (
                      <article
                        className="runtime-container-card"
                        key={container.container_id}
                      >
                        <div className="runtime-container-title">
                          <div>
                            <strong>{container.container_name}</strong>
                            <span>
                              {container.namespace || "namespace 없음"}
                              {container.pod_name
                                ? ` · ${container.pod_name}`
                                : " · Pod 없음"}
                            </span>
                          </div>
                          <StatusBadge value={container.status || "UNKNOWN"} />
                        </div>

                        <div className="runtime-container-metrics">
                          <div>
                            <span>CPU request</span>
                            <strong>
                              {formatCpu(container.cpu_request_millicores)}
                            </strong>
                          </div>
                          <div>
                            <span>Memory request</span>
                            <strong>
                              {formatMib(container.memory_request_mib)}
                            </strong>
                          </div>
                          <div>
                            <span>Storage request</span>
                            <strong>
                              {formatMib(
                                container.ephemeral_storage_request_mib,
                              )}
                            </strong>
                          </div>
                        </div>

                        <div className="runtime-container-meta">
                          <code title={container.container_id}>
                            {container.container_id}
                          </code>
                          <span>{formatDate(container.observed_at)}</span>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

function AdminDashboard({ onLogout }) {
  const [activeView, setActiveView] = useState("accounts");
  const [accounts, setAccounts] = useState([]);
  const [resources, setResources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [resourceLoading, setResourceLoading] = useState(true);
  const [resourceProvider, setResourceProvider] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [actionState, setActionState] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [syncAccountName, setSyncAccountName] = useState("");

  const loadAccounts = useCallback(async () => {
    try {
      const response = await listProviderAccounts();
      setAccounts(response.items);
      setConnected(true);
      setError(null);
    } catch (requestError) {
      setConnected(false);
      setError(requestError);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadResources = useCallback(async () => {
    setResourceLoading(true);
    try {
      const response = await listResourceTargets({
        provider: resourceProvider,
      });
      setResources(response.items);
      setConnected(true);
      setError(null);
    } catch (requestError) {
      setConnected(false);
      setError(requestError);
    } finally {
      setResourceLoading(false);
    }
  }, [resourceProvider]);

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  useEffect(() => {
    loadResources();
  }, [loadResources]);

  const metrics = useMemo(() => {
    const healthy = accounts.filter(
      (account) =>
        account.credential_status === "VALID" &&
        account.permission_status === "SUFFICIENT" &&
        account.provider_api_status === "AVAILABLE",
    ).length;
    const running = resources.filter(
      (resource) => resource.status?.toUpperCase() === "RUNNING",
    ).length;
    const registered = resources.filter((resource) => resource.enabled).length;
    const totalCpuMillicores = resources.reduce(
      (sum, resource) =>
        sum + (resource.provider_capacity?.cpu_millicores || 0),
      0,
    );
    const totalMemoryMib = resources.reduce(
      (sum, resource) => sum + (resource.provider_capacity?.memory_mib || 0),
      0,
    );
    return {
      total: accounts.length,
      healthy,
      scopes: accounts.reduce(
        (sum, account) => sum + accountScopes(account).length,
        0,
      ),
      running,
      registered,
      resourceTotal: resources.length,
      totalCpuMillicores,
      totalMemoryMib,
    };
  }, [accounts, resources]);

  async function handleCreated(account) {
    setNotice(`${account.display_name || account.external_account_id} 계정을 등록했습니다.`);
    await loadAccounts();
  }

  async function handleVerify(account) {
    if (!ACTIVE_PROVIDERS.has(account.provider)) return;
    setError(null);
    setNotice(null);
    setActionState(`verify:${account.account_id}`);
    try {
      const result = await verifyProviderAccount(account.account_id);
      setNotice(
        result.success
          ? `${account.display_name || account.external_account_id} 검증에 성공했습니다.`
          : "검증은 완료됐지만 확인이 필요한 관리 범위가 있습니다.",
      );
      await loadAccounts();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setActionState(null);
    }
  }

  async function handleSync(account) {
    if (!ACTIVE_PROVIDERS.has(account.provider)) return;
    setError(null);
    setNotice(null);
    setActionState(`sync:${account.account_id}`);
    try {
      const result = await syncProviderAccount(account.account_id);
      setSyncResult(result);
      setSyncAccountName(account.display_name || account.external_account_id);
      setNotice(`${result.discovered_count}개의 VM을 동기화했습니다.`);
      await loadAccounts();
      await loadResources();
      setActiveView("resources");
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 502) {
        setSyncResult(requestError.payload);
        setSyncAccountName(account.display_name || account.external_account_id);
        setActiveView("resources");
      }
      setError(requestError);
      await loadAccounts();
    } finally {
      setActionState(null);
    }
  }

  async function handleDelete(account) {
    const accountName = account.display_name || account.external_account_id;
    const confirmed = window.confirm(
      `"${accountName}" 계정과 Broker DB에 저장된 VM snapshot을 삭제할까요?\n\n실제 클라우드 VM은 삭제되지 않습니다.`,
    );
    if (!confirmed) return;

    setError(null);
    setNotice(null);
    setActionState(`delete:${account.account_id}`);
    try {
      const result = await deleteProviderAccount(account.account_id);
      setSyncResult(null);
      setSyncAccountName("");
      setNotice(
        `${accountName} 계정과 VM snapshot ${result.deleted_resource_count}개를 삭제했습니다.`,
      );
      await loadAccounts();
      await loadResources();
    } catch (requestError) {
      setError(requestError);
    } finally {
      setActionState(null);
    }
  }

  async function handleCandidateRegistration(resource, enabled) {
    const resourceName = resource.name || resource.instance_id;
    if (
      !enabled &&
      !window.confirm(
        `"${resourceName}" VM을 후보 등록에서 해제할까요?\n\n신규 배치 후보에서만 제외되며 실행 중인 workload와 reservation은 변경하지 않습니다.`,
      )
    ) {
      return;
    }

    setError(null);
    setNotice(null);
    setActionState(`candidate:${resource.resource_target_id}`);
    try {
      const updated = await updateResourceTargetCandidateRegistration(
        resource.resource_target_id,
        enabled,
      );
      setResources((current) =>
        current.map((item) =>
          item.resource_target_id === updated.resource_target_id
            ? updated
            : item,
        ),
      );
      setNotice(
        enabled
          ? `${resourceName} VM을 Scheduler 후보로 등록했습니다.`
          : `${resourceName} VM의 Scheduler 후보 등록을 해제했습니다.`,
      );
    } catch (requestError) {
      setError(requestError);
    } finally {
      setActionState(null);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark">MB</div>
          <div>
            <strong>MSG Broker</strong>
            <span>Cloud control plane</span>
          </div>
        </div>
        <div className="topbar-actions">
          <div className={`connection-state ${connected ? "is-online" : "is-offline"}`}>
            <span aria-hidden="true" />
            {connected ? "Broker API 연결됨" : "Broker API 연결 끊김"}
          </div>
          <button
            className="button button-topbar"
            type="button"
            onClick={onLogout}
          >
            로그아웃
          </button>
        </div>
      </header>

      <main>
        <nav className="file-navigation" aria-label="관리 화면">
          <button
            className={`file-tab ${activeView === "accounts" ? "is-active" : ""}`}
            type="button"
            aria-current={activeView === "accounts" ? "page" : undefined}
            onClick={() => setActiveView("accounts")}
          >
            <span className="file-icon" aria-hidden="true" />
            <span>
              <strong>계정 관리</strong>
              <small>{metrics.total} accounts</small>
            </span>
          </button>
          <button
            className={`file-tab ${activeView === "resources" ? "is-active" : ""}`}
            type="button"
            aria-current={activeView === "resources" ? "page" : undefined}
            onClick={() => setActiveView("resources")}
          >
            <span className="file-icon" aria-hidden="true" />
            <span>
              <strong>전체 VM</strong>
              <small>{metrics.resourceTotal} resources</small>
            </span>
          </button>
        </nav>

        <div className="view-surface">
          {notice && (
            <div className="notice notice-success" role="status">
              <strong>완료</strong>
              <p>{notice}</p>
              <button type="button" onClick={() => setNotice(null)}>닫기</button>
            </div>
          )}
          <ErrorBanner error={error} onClose={() => setError(null)} />

          {activeView === "accounts" ? (
            <>
              <div className="view-title">
                <div>
                  <span className="eyebrow">Provider accounts</span>
                  <h1>계정 관리</h1>
                </div>
                <span>등록 · 검증 · 동기화 · 삭제</span>
              </div>

              <section className="metric-grid metric-grid-accounts" aria-label="계정 요약">
                <article><span>등록 계정</span><strong>{metrics.total}</strong><small>all provider accounts</small></article>
                <article><span>정상 계정</span><strong>{metrics.healthy}</strong><small>인증·권한·API 정상</small></article>
                <article><span>관리 범위</span><strong>{metrics.scopes}</strong><small>projects · regions · subscriptions</small></article>
              </section>

              <div className="workspace-grid">
                <RegisterForm onCreated={handleCreated} />

                <section className="panel accounts-panel" aria-labelledby="accounts-title">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">Provider inventory</span>
                      <h2 id="accounts-title">등록된 계정</h2>
                    </div>
                    <button className="button button-ghost" type="button" onClick={loadAccounts}>
                      새로고침
                    </button>
                  </div>
                  {loading ? (
                    <div className="loading-state">계정 목록을 불러오는 중입니다...</div>
                  ) : (
                    <AccountTable
                      accounts={accounts}
                      actionState={actionState}
                      onVerify={handleVerify}
                      onSync={handleSync}
                      onDelete={handleDelete}
                    />
                  )}
                </section>
              </div>
            </>
          ) : (
            <>
              <div className="view-title">
                <div>
                  <span className="eyebrow">Resource targets</span>
                  <h1>전체 VM</h1>
                </div>
                <span>Broker DB snapshot</span>
              </div>

              <section className="metric-grid metric-grid-resources" aria-label="VM 요약">
                <article><span>조회된 VM</span><strong>{metrics.resourceTotal}</strong><small>현재 필터 기준</small></article>
                <article><span>실행 중 VM</span><strong>{metrics.running}</strong><small>provider state RUNNING</small></article>
                <article><span>후보 등록 VM</span><strong>{metrics.registered}</strong><small>enabled=true</small></article>
                <article><span>전체 CPU</span><strong>{formatCpu(metrics.totalCpuMillicores)}</strong><small>provider capacity 합계</small></article>
                <article><span>전체 Memory</span><strong>{formatMib(metrics.totalMemoryMib)}</strong><small>provider capacity 합계</small></article>
              </section>

              <SyncResult result={syncResult} accountName={syncAccountName} />
              <ResourceInventory
                resources={resources}
                loading={resourceLoading}
                provider={resourceProvider}
                actionState={actionState}
                onProviderChange={setResourceProvider}
                onCandidateRegistrationChange={handleCandidateRegistration}
                onRefresh={loadResources}
              />
            </>
          )}
        </div>
      </main>

      <footer>
        <span>MSG Resource Broker</span>
        <span>관리자 인증으로 보호됨</span>
      </footer>
    </div>
  );
}

export default function App() {
  const [authenticated, setAuthenticated] = useState(
    () => Boolean(getAdminToken()),
  );

  useEffect(() => {
    function handleExpiredAuthentication() {
      setAuthenticated(false);
    }

    window.addEventListener(
      "admin-auth-expired",
      handleExpiredAuthentication,
    );
    return () => {
      window.removeEventListener(
        "admin-auth-expired",
        handleExpiredAuthentication,
      );
    };
  }, []);

  function handleLogout() {
    clearAdminToken();
    setAuthenticated(false);
  }

  if (!authenticated) {
    return <LoginScreen onAuthenticated={() => setAuthenticated(true)} />;
  }

  return <AdminDashboard onLogout={handleLogout} />;
}
