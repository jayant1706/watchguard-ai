# WatchGuard AI — Browser Extension Setup

This extension connects your browser to the WatchGuard AI desktop app,
giving it full control over ANY video: YouTube, Netflix, Prime Video,
Disney+, Hotstar, Hulu, or any website with a video player.

---

## Install in Chrome / Edge / Brave (2 minutes)

1. Open your browser and go to:
   `chrome://extensions`  (or `edge://extensions`)

2. Turn ON **Developer mode** (toggle in top-right corner)

3. Click **"Load unpacked"**

4. Select the `browser_extension` folder inside your WatchGuard AI folder

5. The extension icon (⬡) appears in your toolbar — you're done!

---

## How it works

```
Your Webcam → WatchGuard AI (Python)
                    ↕  WebSocket ws://localhost:8765
           Browser Extension (background.js)
                    ↕  Chrome messaging
           Content Script (content.js) injected into tab
                    ↕  Direct JS access
           video.currentTime  ← reads position, seeks, pauses, plays
```

- Python app starts a WebSocket server on port 8765
- Extension connects automatically when WatchGuard AI is running
- Content script finds the `<video>` element on any page
- Python can then: get position, pause, play, seek to any second

---

## Supported sites (any site with HTML5 video)

| Site | Pause | Play | Seek | Notes |
|---|---|---|---|---|
| YouTube | ✓ | ✓ | ✓ | Full support |
| Netflix | ✓ | ✓ | ✓ | Full support |
| Prime Video | ✓ | ✓ | ✓ | Full support |
| Disney+ | ✓ | ✓ | ✓ | Full support |
| Hotstar | ✓ | ✓ | ✓ | Full support |
| Hulu | ✓ | ✓ | ✓ | Full support |
| Any HTML5 video | ✓ | ✓ | ✓ | Full support |

---

## Troubleshooting

**Extension badge shows "OFF"**
→ Make sure WatchGuard AI is running first, then the extension reconnects automatically

**"No video tab found"**
→ Make sure your video is playing in a browser tab (not a standalone app)
→ Click the ⬡ icon to check status

**Seek button says "Browser Extension Required"**
→ The extension isn't connected yet — check the bridge status in the app

**Netflix/Prime blocks seek**
→ These sites sometimes override `video.currentTime` — try clicking the
  seek button once manually if the auto-seek doesn't work on first try
