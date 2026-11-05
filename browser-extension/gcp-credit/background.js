const BILLING_ACCOUNTS = new Set([
  "0124C6-B5192B-C27C7B",
  "01E3CD-ECCF6E-88C393",
  "01F61C-AE5ABA-27D1C6",
  "01C3E3-425D2E-4B1549",
  "01D34A-E63AC9-B60A28",
  "01BEEE-426690-6BBECC",
  "01EB38-622C8D-FAA79B",
  "01BEE8-C67E69-0F9D51",
  "010153-E591DC-5EA9E7",
]);

async function getCredit(billingAccountId) {
  let tab;
  try {
    tab = await chrome.tabs.create({
      url: `https://console.cloud.google.com/billing/${billingAccountId}/credits/all?supportedpurview=project`,
      active: false,
    });
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const result = await chrome.tabs.sendMessage(
        tab.id,
        { type: "GCP_CREDIT_READ", billingAccountId },
      ).catch(() => null);
      if (result?.amount) return result;
    }
  } catch {
    // A missing session, denied page, or unavailable tab is reported as unreadable.
  } finally {
    if (tab) await chrome.tabs.remove(tab.id).catch(() => {});
  }
  return null;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== "GCP_CREDIT_READ"
    || !sender.url?.startsWith("https://broker.mjsec.kr/")) return;
  if (!BILLING_ACCOUNTS.has(message.billingAccountId)) {
    sendResponse(null);
    return;
  }
  getCredit(message.billingAccountId).then(sendResponse);
  return true;
});
