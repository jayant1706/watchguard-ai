function fmt(s) {
  if (s == null || isNaN(s)) return "--:--";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`
           : `${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
}

// Ask background for latest cache
chrome.runtime.sendMessage({ type: "PING" }, (resp) => {
  const pyEl    = document.getElementById("py-status");
  const vidEl   = document.getElementById("video-status");

  if (chrome.runtime.lastError || !resp) {
    pyEl.textContent = "Not connected";
    pyEl.className   = "value red";
    return;
  }

  pyEl.textContent = resp.hasVideo !== undefined ? "Connected ✓" : "Waiting...";
  pyEl.className   = "value green";
  vidEl.textContent = resp.hasVideo ? "Yes ✓" : "No";
  vidEl.className   = resp.hasVideo ? "value green" : "value red";
});

// Get position from active tab
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (!tabs[0]) return;
  chrome.tabs.sendMessage(tabs[0].id, { type: "GET_POSITION" }, (resp) => {
    if (chrome.runtime.lastError || !resp) return;
    document.getElementById("position").textContent =
      resp.currentTime != null ? fmt(resp.currentTime) + " / " + fmt(resp.duration) : "—";
    document.getElementById("page-title").textContent = resp.title || "—";
    const vidEl = document.getElementById("video-status");
    vidEl.textContent = resp.ok ? "Yes ✓" : "No";
    vidEl.className   = resp.ok ? "value green" : "value red";
  });
});
