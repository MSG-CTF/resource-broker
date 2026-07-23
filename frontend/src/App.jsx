import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  createProviderAccount,
  listProviderAccounts,
  syncProviderAccount,
  verifyProviderAccount,
} from "./api/providerAccounts.js";

const PROVIDER_DEFINITIONS = {
  GCP: {
    label: "Google Cloud",
    shortLabel: "GCP",
    avatar: "G",
    accountLabel: "Gmail 계정",
    accountPlaceholder: "example@gmail.com",
    accountType: "email",
    accountHelp: "실제 클라우드 계정을 식별하는 Gmail 주소입니다.",
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
        label: "Role ARN",
        placeholder: "arn:aws:iam::123456789012:role/msg-broker-readonly",
        help: "브로커가 AssumeRole할 읽기 전용 IAM Role입니다.",
        kind: "text",
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
  OCI: {
    label: "Oracle Cloud Infrastructure",
    shortLabel: "OCI",
    avatar: "O",
    accountLabel: "Tenancy OCID",
    accountPlaceholder: "ocid1.tenancy.oc1...",
    accountType: "text",
    accountHelp: "OCI 계정을 식별하는 Tenancy OCID입니다.",
    configFields: [
      {
        name: "compartmentIds",
        configKey: "compartment_ids",
        label: "Compartment OCIDs",
        placeholder: "ocid1.compartment.oc1...",
        help: "Compute 인스턴스를 조회할 Compartment를 입력하세요.",
        kind: "list",
      },
      {
        name: "regions",
        configKey: "regions",
        label: "OCI Regions",
        placeholder: "ap-chuncheon-1",
        help: "조회할 OCI Region을 입력하세요.",
        kind: "list",
      },
    ],
  },
  NCP: {
    label: "NAVER Cloud Platform",
    shortLabel: "NCP",
    avatar: "N",
    accountLabel: "계정/Sub Account 식별자",
    accountPlaceholder: "ncp-account-01",
    accountType: "text",
    accountHelp: "팀에서 관리할 NCP 계정 또는 Sub Account 식별자입니다.",
    configFields: [
      {
        name: "regionCodes",
        configKey: "region_codes",
        label: "NCP Region Codes",
        placeholder: "KR\nSGN",
        help: "Server를 조회할 Region Code를 입력하세요.",
        kind: "list",
      },
    ],
  },
};

const PROVIDER_ORDER = ["GCP", "AWS", "AZURE", "OCI", "NCP"];
const ACTIVE_PROVIDERS = new Set(["GCP"]);

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
  return Object.fromEntries(
    PROVIDER_DEFINITIONS[provider].configFields.map((field) => [
      field.configKey,
      field.kind === "list"
        ? splitList(configValues[field.name] || "")
        : (configValues[field.name] || "").trim(),
    ]),
  );
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
    case "OCI":
      return config.compartment_ids || [];
    case "NCP":
      return config.region_codes || [];
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

    const externalAccountId =
      form.provider === "GCP"
        ? form.externalAccountId.trim().toLowerCase()
        : form.externalAccountId.trim();

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
                required
              />
            ) : (
              <input
                type="text"
                name={field.name}
                value={form.configValues[field.name] || ""}
                onChange={updateConfigField}
                placeholder={field.placeholder}
                required
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

function AccountTable({ accounts, actionState, onVerify, onSync }) {
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
    <section className="panel resources-panel" aria-labelledby="resources-title">
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

      {result.resources.length > 0 ? (
        <div className="table-wrap resource-table-wrap">
          <table>
            <thead>
              <tr>
                <th>VM</th>
                <th>위치</th>
                <th>사양</th>
                <th>네트워크</th>
                <th>상태</th>
              </tr>
            </thead>
            <tbody>
              {result.resources.map((resource) => (
                <tr key={resource.resource_target_id}>
                  <td>
                    <strong>{resource.name}</strong>
                    <span className="subtle-text">ID {resource.instance_id}</span>
                  </td>
                  <td>
                    <strong>{resource.region}</strong>
                    <span className="subtle-text">{resource.zone}</span>
                  </td>
                  <td><code>{resource.machine_type}</code></td>
                  <td>
                    <span>{resource.internal_ip || "내부 IP 없음"}</span>
                    <span className="subtle-text">
                      {resource.external_ip || "외부 IP 없음"}
                    </span>
                  </td>
                  <td><StatusBadge value={resource.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty-inline">조회된 VM이 없습니다.</div>
      )}
    </section>
  );
}

export default function App() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
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

  useEffect(() => {
    loadAccounts();
  }, [loadAccounts]);

  const metrics = useMemo(() => {
    const healthy = accounts.filter(
      (account) =>
        account.credential_status === "VALID" &&
        account.permission_status === "SUFFICIENT" &&
        account.provider_api_status === "AVAILABLE",
    ).length;
    const running = syncResult?.resources.filter(
      (resource) => resource.status?.toUpperCase() === "RUNNING",
    ).length;
    return {
      total: accounts.length,
      healthy,
      scopes: accounts.reduce(
        (sum, account) => sum + accountScopes(account).length,
        0,
      ),
      running: running ?? "—",
    };
  }, [accounts, syncResult]);

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
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 502) {
        setSyncResult(requestError.payload);
        setSyncAccountName(account.display_name || account.external_account_id);
      }
      setError(requestError);
      await loadAccounts();
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
        <div className={`connection-state ${connected ? "is-online" : "is-offline"}`}>
          <span aria-hidden="true" />
          {connected ? "Broker API 연결됨" : "Broker API 연결 끊김"}
        </div>
      </header>

      <main>
        <section className="hero">
          <div>
            <span className="eyebrow">Provider operations</span>
            <h1>멀티클라우드 계정과 VM을<br />한곳에서 관리하세요.</h1>
            <p>
              Provider별 계정 설정을 등록하고, 준비된 어댑터를 통해 인증 검증과
              VM 목록 동기화를 수행합니다.
            </p>
          </div>
          <div className="hero-note">
            <span>Adapter status</span>
            <strong>GCP 활성 · 4개 준비 중</strong>
            <p>AWS, Azure, OCI, NCP는 계정 계약과 등록 화면을 먼저 제공합니다.</p>
          </div>
        </section>

        <section className="metric-grid" aria-label="계정 요약">
          <article><span>등록 계정</span><strong>{metrics.total}</strong><small>all provider accounts</small></article>
          <article><span>정상 계정</span><strong>{metrics.healthy}</strong><small>인증·권한·API 정상</small></article>
          <article><span>관리 범위</span><strong>{metrics.scopes}</strong><small>projects · regions · subscriptions</small></article>
          <article><span>실행 중 VM</span><strong>{metrics.running}</strong><small>최근 Sync 결과 기준</small></article>
        </section>

        {notice && (
          <div className="notice notice-success" role="status">
            <strong>완료</strong>
            <p>{notice}</p>
            <button type="button" onClick={() => setNotice(null)}>닫기</button>
          </div>
        )}
        <ErrorBanner error={error} onClose={() => setError(null)} />

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
              />
            )}
          </section>
        </div>

        <SyncResult result={syncResult} accountName={syncAccountName} />
      </main>

      <footer>
        <span>MSG Resource Broker</span>
        <span>Admin 인증은 연동 테스트 후 추가 예정</span>
      </footer>
    </div>
  );
}
