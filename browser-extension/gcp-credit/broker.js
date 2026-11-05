window.addEventListener("message", (event) => {
  const data = event.data;
  if (event.source !== window || event.origin !== location.origin
    || data?.source !== "msg-broker-credit-page"
    || data.type !== "GCP_CREDIT_REQUEST"
    || typeof data.requestId !== "string"
    || !/^[0-9A-Z]{6}(?:-[0-9A-Z]{6}){2}$/.test(data.billingAccountId)) return;

  window.postMessage({
    source: "msg-broker-credit-extension",
    type: "GCP_CREDIT_ACK",
    requestId: data.requestId,
  }, location.origin);

  chrome.runtime.sendMessage({ type: "GCP_CREDIT_READ", billingAccountId: data.billingAccountId }, (result) => {
    const failed = chrome.runtime.lastError || !result?.amount;
    window.postMessage({
      source: "msg-broker-credit-extension",
      type: "GCP_CREDIT_RESULT",
      requestId: data.requestId,
      ...(failed ? {} : { amount: result.amount, readAt: result.readAt }),
    }, location.origin);
  });
});
