import { broadcastResponseToMainFrame } from "@azure/msal-browser/redirect-bridge";

broadcastResponseToMainFrame().catch(() => {
  document.body.textContent = "Microsoft 로그인을 전달하지 못했습니다. 이 창을 닫고 브로커에서 다시 연결하세요.";
});
