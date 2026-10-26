import { useEffect, useRef, useState } from "react";
import { PublicClientApplication } from "@azure/msal-browser";

import { connectAzureUserLogin, getAzureUserLogin } from "./api/providerAccounts.js";

export default function AzureAccountLogin({ account, onClose, onConnected }) {
  const [login, setLogin] = useState(null);
  const [ready, setReady] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef(null);
  const client = useRef(null);
  const controller = useRef(null);
  const redirectUri = `${window.location.origin}/azure-auth-redirect.html`;

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;

    async function prepare() {
      try {
        const response = await getAzureUserLogin(account.account_id, { signal: abort.signal });
        if (abort.signal.aborted) return;
        setLogin(response);
        if (!response.client_id) return;
        const instance = new PublicClientApplication({
          auth: {
            clientId: response.client_id,
            authority: `https://login.microsoftonline.com/${response.tenant_id}`,
            redirectUri,
          },
          cache: { cacheLocation: "memoryStorage" },
        });
        await instance.initialize();
        if (abort.signal.aborted) return;
        client.current = instance;
        setReady(true);
      } catch (requestError) {
        if (!abort.signal.aborted) {
          setError("Microsoft 연결 설정을 불러오지 못했습니다. 창을 닫고 다시 시도하세요.");
        }
      }
    }

    prepare();
    return () => {
      abort.abort();
      client.current = null;
    };
  }, [account.account_id, redirectUri]);

  async function handleLogin() {
    if (!client.current || connecting) return;
    const abort = controller.current;
    setConnecting(true);
    setError("");
    try {
      const result = await client.current.acquireTokenPopup({
        scopes: ["https://management.azure.com/.default"],
        prompt: "select_account",
      });
      if (abort.signal.aborted) return;
      if (!result.accessToken || !result.expiresOn) throw new Error("Missing Azure token.");
      await connectAzureUserLogin(
        account.account_id, result.accessToken,
        Math.floor(result.expiresOn.getTime() / 1000), { signal: abort.signal },
      );
      if (!abort.signal.aborted) await onConnected(account);
    } catch (requestError) {
      if (abort.signal.aborted) return;
      const code = String(requestError.errorCode || "");
      const aadsts = String(requestError.errorMessage || "").match(/AADSTS\d+/)?.[0];
      if (code === "user_cancelled") {
        setError("Microsoft 로그인이 취소되었습니다. 다시 로그인할 수 있습니다.");
      } else if (["popup_window_error", "empty_window_error"].includes(code)) {
        setError("로그인 창을 열지 못했습니다. 이 사이트의 팝업을 허용하고 다시 시도하세요.");
      } else {
        setError(`Microsoft 연결에 실패했습니다${aadsts ? ` (${aadsts})` : ""}. 계정 권한과 테넌트의 앱 승인 설정을 확인하세요.`);
      }
    } finally {
      if (!abort.signal.aborted) setConnecting(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Escape") onClose();
    if (event.key !== "Tab") return;
    const controls = dialog.current.querySelectorAll("button:not([disabled]), a[href]");
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div className="resource-modal-backdrop" onKeyDown={handleKeyDown}>
      <section className="resource-modal azure-login-modal" role="dialog" aria-modal="true" aria-labelledby="azure-login-title" ref={dialog}>
        <header className="resource-modal-header">
          <div>
            <span className="eyebrow">Microsoft Azure</span>
            <h2 id="azure-login-title">Microsoft 계정 연결</h2>
          </div>
          <button className="resource-modal-close" type="button" onClick={onClose} aria-label="닫기" autoFocus>×</button>
        </header>
        <div className="resource-modal-body azure-login-body">
          <strong>{account.display_name || account.external_account_id}</strong>
          <p>이 테넌트의 VM 조회 권한을 받은 Microsoft 계정으로 로그인하세요.</p>
          <div role="status" aria-live="polite">
            {!login && !error && <p>Microsoft 로그인 연결을 준비하는 중입니다...</p>}
            {login && !login.client_id && (
              <>
                <p>{login.message || "중앙 브로커용 Microsoft 앱 설정이 필요합니다."}</p>
                <ol>
                  <li>Microsoft Entra 앱 등록에서 여러 조직의 계정을 허용하는 중앙 앱을 만듭니다.</li>
                  <li>단일 페이지 애플리케이션(SPA)의 리디렉션 URI에 아래 주소를 등록합니다.</li>
                </ol>
                <code style={{ overflowWrap: "anywhere" }}>{redirectUri}</code>
                <p>API 권한에서 Azure Service Management의 위임 권한 <code>user_impersonation</code>을 추가합니다.</p>
                <p>중앙 앱의 Client ID를 브로커에 한 번 설정하면 각 계정에 따로 입력할 필요가 없습니다.</p>
                <a href="https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app" target="_blank" rel="noopener noreferrer">Microsoft 앱 등록 안내</a>
              </>
            )}
            {login?.client_id && (
              <>
                {login.status === "CONNECTED" && <p>이 테넌트에 로그인 연결이 있습니다. 다른 계정으로 다시 연결할 수도 있습니다.</p>}
                <button className="button button-primary" type="button" disabled={!ready || connecting} onClick={handleLogin}>
                  {connecting ? "Microsoft 로그인 진행 중..." : "Microsoft 계정으로 로그인"}
                </button>
                <p>새 창에서 로그인과 MFA를 완료하면 VM 목록을 자동으로 동기화합니다.</p>
              </>
            )}
            {error && <p role="alert">{error}</p>}
          </div>
          <small>로그인 연결은 이 테넌트에 적용됩니다. 서버가 재시작되거나 액세스 토큰이 만료되면 다시 연결하세요.</small>
        </div>
      </section>
    </div>
  );
}
