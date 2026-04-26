/**
 * WatchGuard AI - Background Service Worker
 *
 * Maintains a WebSocket connection to the Python app (ws://localhost:8765).
 * Routes commands from Python → content script in the active video tab.
 * Forwards position updates from content script → Python.
 */

"use strict";

const WS_URL        = "ws://localhost:8765";
const RECONNECT_MS  = 3000;

let ws              = null;
let activeVideoTab  = null;   // tab id where we last saw a video
let connected       = false;
let positionCache   = null;   // last known position

// ── WebSocket connection ───────────────────────────────────────────

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  console.log("[WatchGuard BG] Connecting to Python bridge...");
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    connected = true;
    console.log("[WatchGuard BG] Connected to WatchGuard AI ✓");
    ws.send(JSON.stringify({ type: "EXTENSION_READY", version: "1.0" }));
    updateBadge("ON");
  };

  ws.onclose = () => {
    connected = false;
    updateBadge("OFF");
    console.log("[WatchGuard BG] Disconnected — retrying in 3s");
    setTimeout(connect, RECONNECT_MS);
  };

  ws.onerror = (e) => {
    console.warn("[WatchGuard BG] WS error:", e.message || e);
  };

  ws.onmessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); }
    catch { return; }

    console.log("[WatchGuard BG] ← Python:", msg.type);

    switch (msg.type) {

      case "GET_POSITION": {
        const result = await sendToVideoTab({ type: "GET_POSITION" });
        ws.send(JSON.stringify({
          type: "POSITION_RESPONSE",
          ...(result || {}),
          tabId: activeVideoTab,
        }));
        break;
      }

      case "PAUSE": {
        const result = await sendToVideoTab({ type: "PAUSE" });
        ws.send(JSON.stringify({ type: "PAUSE_RESPONSE", ...(result || {}) }));
        break;
      }

      case "PLAY": {
        const result = await sendToVideoTab({ type: "PLAY" });
        ws.send(JSON.stringify({ type: "PLAY_RESPONSE", ...(result || {}) }));
        break;
      }

      case "SEEK_AND_PLAY": {
        const result = await sendToVideoTab({
          type: "SEEK_AND_PLAY",
          position: msg.position,
        });
        ws.send(JSON.stringify({ type: "SEEK_RESPONSE", ...(result || {}) }));
        break;
      }

      case "SEEK": {
        const result = await sendToVideoTab({
          type: "SEEK",
          position: msg.position,
        });
        ws.send(JSON.stringify({ type: "SEEK_RESPONSE", ...(result || {}) }));
        break;
      }

      case "PING":
        ws.send(JSON.stringify({ type: "PONG", hasVideo: !!activeVideoTab }));
        break;
    }
  };
}

// ── Send message to the tab that has the video ────────────────────

async function sendToVideoTab(msg) {
  // Find the best tab to send to
  const tabId = await findVideoTab();
  if (!tabId) {
    console.warn("[WatchGuard BG] No video tab found");
    return { ok: false, error: "No video tab found" };
  }
  try {
    const response = await chrome.tabs.sendMessage(tabId, msg);
    return response;
  } catch (e) {
    console.warn("[WatchGuard BG] Tab message failed:", e.message);
    activeVideoTab = null;
    return { ok: false, error: e.message };
  }
}

async function findVideoTab() {
  // First try the cached tab
  if (activeVideoTab) {
    try {
      const r = await chrome.tabs.sendMessage(activeVideoTab, { type: "PING" });
      if (r && r.hasVideo) return activeVideoTab;
    } catch {}
    activeVideoTab = null;
  }

  // Scan all tabs for a video
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.id || tab.url?.startsWith("chrome://")) continue;
    try {
      const r = await chrome.tabs.sendMessage(tab.id, { type: "PING" });
      if (r && r.hasVideo) {
        activeVideoTab = tab.id;
        return tab.id;
      }
    } catch {}
  }
  return null;
}

// ── Receive position updates from content scripts ─────────────────

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type === "POSITION_UPDATE") {
    positionCache = msg;
    // Track which tab has the most recent video activity
    if (msg.duration > 0) {
      activeVideoTab = sender.tab?.id || activeVideoTab;
    }
    // Forward to Python if connected
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({
          type: "POSITION_UPDATE",
          currentTime: msg.currentTime,
          duration: msg.duration,
          paused: msg.paused,
          url: msg.url,
          title: msg.title,
          tabId: sender.tab?.id,
        }));
      } catch {}
    }
  }
});

// ── Badge helper ──────────────────────────────────────────────────

function updateBadge(text) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({
    color: text === "ON" ? "#00e676" : "#ff4444",
  });
}

// ── Boot ──────────────────────────────────────────────────────────

connect();
updateBadge("OFF");
