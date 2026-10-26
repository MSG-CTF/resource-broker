import { useRef, useState } from "react";

import { configureAzureManagedIdentity } from "./api/providerAccounts.js";

export default function AzureManagedIdentitySetup({ account, onClose, onSaved }) {
  const [clientId, setClientId] = useState(account.config?.client_id || "");
  const [groups, setGroups] = useState(
    account.config?.resource_groups?.map(
      (group) => `${group.subscription_id}/${group.name}`,
    ).join("\n") || account.config?.subscription_ids?.map((id) => `${id}/`).join("\n") || "",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef(null);

  async function handleSubmit(event) {
    event.preventDefault();
    const resourceGroups = groups.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
      const slash = line.indexOf("/");
      return slash < 1 ? null : {
        subscription_id: line.slice(0, slash).trim(),
        name: line.slice(slash + 1).trim(),
      };
    });
    if (resourceGroups.some((group) => !group?.subscription_id || !group.name)) {
      setError("각 줄을 Subscription ID/리소스 그룹 이름 형식으로 입력하세요.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const result = await configureAzureManagedIdentity(account.account_id, {
        client_id: clientId.trim(),
        resource_groups: resourceGroups,
      });
      await onSaved(account, result);
    } catch (requestError) {
      const failures = requestError.payload?.scopes?.filter((scope) => !scope.success) || [];
      setError(failures.length
        ? failures.map((scope) => `${scope.scope_id}: ${scope.message || scope.error_code}`).join("\n")
        : requestError.message);
    } finally {
      setSubmitting(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Escape") onClose();
    if (event.key !== "Tab") return;
    const controls = dialog.current.querySelectorAll("button:not([disabled]), input:not([disabled]), textarea:not([disabled])");
    if (event.shiftKey && document.activeElement === controls[0]) {
      event.preventDefault();
      controls[controls.length - 1].focus();
    } else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) {
      event.preventDefault();
      controls[0].focus();
    }
  }

  return (
    <div className="resource-modal-backdrop" onKeyDown={handleKeyDown}>
      <section className="resource-modal azure-login-modal" role="dialog" aria-modal="true" aria-labelledby="azure-managed-title" ref={dialog}>
        <header className="resource-modal-header">
          <h2 id="azure-managed-title">Azure 자동 연동</h2>
          <button className="resource-modal-close" type="button" onClick={onClose} aria-label="닫기">×</button>
        </header>
        <form className="resource-modal-body azure-login-body" onSubmit={handleSubmit}>
          <strong>{account.display_name || account.external_account_id}</strong>
          <p>각 리소스 그룹의 사용자 할당 관리 ID에 Broker GCP 서비스 계정을 신뢰하도록 설정하고, Reader 권한을 부여한 뒤 입력하세요.</p>
          <label>
            <span>사용자 할당 관리 ID의 Client ID</span>
            <input value={clientId} onChange={(event) => setClientId(event.target.value)} required autoFocus />
          </label>
          <label>
            <span>조회할 리소스 그룹 (한 줄에 하나)</span>
            <textarea value={groups} onChange={(event) => setGroups(event.target.value)} rows="4" required placeholder="Subscription ID/리소스 그룹 이름" />
          </label>
          <small>저장 전에 모든 그룹을 검증하고 동기화합니다. 실패하면 기존 연결과 VM 기록을 유지합니다.</small>
          {error && <p role="alert" style={{ whiteSpace: "pre-line" }}>{error}</p>}
          <button className="button button-primary" disabled={submitting}>
            {submitting ? "연동 확인 중..." : "자동 연동 및 동기화"}
          </button>
        </form>
      </section>
    </div>
  );
}
