/**
 * WatchGuard AI - Content Script
 * Injected into every tab. Finds the main video element and
 * responds to commands from the background WebSocket bridge.
 */

(function () {
  "use strict";

  let video = null;
  let heartbeatInterval = null;
  let positionInterval = null;

  // ── Find the best video on the page ──────────────────────────────

  function findVideo() {
    // Prefer the largest / longest video (the "main" one)
    const all = Array.from(document.querySelectorAll("video"));
    if (all.length === 0) return null;
    return all.reduce((best, v) => {
      if (!best) return v;
      // Prefer non-zero duration and larger area
      const bestScore = (best.duration || 0) * (best.videoWidth * best.videoHeight || 1);
      const vScore    = (v.duration    || 0) * (v.videoWidth * v.videoHeight    || 1);
      return vScore > bestScore ? v : best;
    }, null);
  }

  function attachVideo() {
    const found = findVideo();
    if (found && found !== video) {
      video = found;
      console.log("[WatchGuard] Video attached:", video.src?.slice(0, 60) || "(blob)");
      startPositionReporting();
    }
  }

  // Re-scan for video every 2 s (handles SPAs and lazy loaders)
  setInterval(attachVideo, 2000);
  attachVideo();

  // ── Position reporting → background ──────────────────────────────

  function startPositionReporting() {
    if (positionInterval) clearInterval(positionInterval);
    positionInterval = setInterval(() => {
      if (!video) return;
      chrome.runtime.sendMessage({
        type: "POSITION_UPDATE",
        currentTime: video.currentTime,
        duration: video.duration || 0,
        paused: video.paused,
        url: location.href,
        title: document.title,
      }).catch(() => {}); // tab may not be active
    }, 500);
  }

  // ── Commands from background ──────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!video) {
      // Try one more time
      attachVideo();
    }

    switch (msg.type) {

      case "GET_POSITION":
        sendResponse({
          ok: !!video,
          currentTime: video ? video.currentTime : null,
          duration: video ? (video.duration || 0) : null,
          paused: video ? video.paused : null,
          url: location.href,
          title: document.title,
        });
        break;

      case "PAUSE":
        if (video && !video.paused) {
          video.pause();
          sendResponse({ ok: true, currentTime: video.currentTime });
        } else {
          sendResponse({ ok: !!video });
        }
        break;

      case "PLAY":
        if (video && video.paused) {
          video.play().then(() => {
            sendResponse({ ok: true, currentTime: video.currentTime });
          }).catch(e => {
            sendResponse({ ok: false, error: String(e) });
          });
          return true; // async
        } else {
          sendResponse({ ok: !!video });
        }
        break;

      case "SEEK":
        if (video && msg.position != null) {
          video.currentTime = msg.position;
          sendResponse({ ok: true, currentTime: video.currentTime });
        } else {
          sendResponse({ ok: false, error: "No video or no position" });
        }
        break;

      case "SEEK_AND_PLAY":
        if (video && msg.position != null) {
          video.currentTime = msg.position;
          setTimeout(() => {
            video.play().then(() => {
              sendResponse({ ok: true, currentTime: video.currentTime });
            }).catch(e => {
              sendResponse({ ok: false, error: String(e) });
            });
          }, 200);
          return true; // async
        } else {
          sendResponse({ ok: false, error: "No video or no position" });
        }
        break;

      case "PING":
        sendResponse({ ok: true, hasVideo: !!video });
        break;
    }
    return true; // keep channel open for async
  });

  // ── YouTube-specific: disable autoplay on resume ──────────────────
  // YouTube sometimes interferes with video.play() — use keyboard shortcut fallback
  function ytFallbackKey(key) {
    const el = document.querySelector(".html5-video-player");
    if (el) el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  }

})();
