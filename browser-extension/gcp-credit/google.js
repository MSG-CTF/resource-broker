chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "GCP_CREDIT_READ"
    || location.pathname !== `/billing/${message.billingAccountId}/credits/all`) return;
  // shortcut: Google Console 표 구조에 의존하므로 열 구성이 바뀌면 이 선택자를 갱신한다.
  const freeTrialRows = [...document.querySelectorAll('[role="treegrid"] [role="row"]')]
    .map((row) => [...row.querySelectorAll('[role="gridcell"]')])
    .filter((cells) => cells[6]?.textContent?.includes(`FreeTrial`) && cells[6].textContent.includes(`Credit-${message.billingAccountId}`));
  const activeRow = freeTrialRows.find((cells) => cells[1]?.querySelector('.cfc-icon-status-success'));
  const amount = activeRow?.[3]?.textContent?.trim() || (freeTrialRows.length ? "₩0" : null);
  sendResponse(amount && amount.length <= 32 && /\d/.test(amount)
    ? { amount, readAt: new Date().toISOString() }
    : null);
});
