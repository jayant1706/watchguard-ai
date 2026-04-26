<div align="center">

# 🛡️ WatchGuard AI

**Never miss a single second of your video again.**

WatchGuard AI uses your webcam and face detection to automatically pause videos when you look away — and resumes from the exact moment you missed when you return.

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](https://python.org)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.8%2B-green?logo=opencv&logoColor=white)](https://opencv.org)
[![License](https://img.shields.io/badge/License-MIT-purple)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](https://github.com)

</div>

---

## 🎬 What is WatchGuard AI?

WatchGuard AI is a smart desktop application that watches you while you watch videos. Using your webcam and OpenCV's real-time face detection, it detects when you look away from the screen — and instantly pauses your video. When you look back, it seeks back to the exact timestamp you left at and resumes playback automatically.

Works on **YouTube, Netflix, Prime Video, Disney+, Hotstar, Hulu**, and any website with an HTML5 video player — through a lightweight browser extension.

---

## ✨ Features

- 🎯 **Real-time face detection** using OpenCV Haar Cascade — no cloud, no GPU needed
- ⏸️ **Auto-pause** when you look away, **auto-resume** from the exact missed moment
- 🌐 **Browser extension** for full control over any HTML5 video site
- 📊 **Session logging** — tracks every away event with video timestamps
- 🤖 **Built-in AI Chat** — powered by Groq Cloud (Llama 3.3), xAI Grok, or Anthropic Claude
- 📁 **Export logs** as JSON or CSV for your watch history
- 🎛️ **Adjustable sensitivity** and away threshold via a clean desktop UI
- 🔒 **100% local** — face detection runs entirely on your machine

---

## 🖼️ How It Works

```
Your Webcam
     │
     ▼
WatchGuard AI (Python Desktop App)
  ├── OpenCV Face Detector  →  Is the user watching?
  ├── Session Logger        →  Tracks away events + video timestamps
  ├── AI Chat (Groq/Grok/Claude)
  └── WebSocket Server (ws://localhost:8765)
          │
          ▼
  Browser Extension (Chrome/Edge/Brave)
          │
          ▼
  Content Script injected into tab
          │
          ▼
  video.currentTime  ←  pause / play / seek
```

1. The Python app opens your webcam and checks for a face every 500ms
2. If no face is detected for N seconds (configurable), the video is paused
3. The exact video position is saved at the moment you looked away
4. When your face is detected again, the video seeks back to that timestamp and resumes
5. Every away event is logged with wall clock time, video position, and duration

---

## 🚀 Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/watchguard-ai.git
cd watchguard-ai
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
pip install groq   # for Groq Cloud AI chat
```

**Requirements:**
- Python 3.8 or higher
- A webcam
- Chrome, Edge, or Brave browser

### 3. Run the app

```bash
python main.py
```

### 4. Install the browser extension

1. Open your browser and go to `chrome://extensions`
2. Turn on **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select the `browser_extension/` folder
5. The **⬡** icon appears in your toolbar — you're connected!

---

## 📁 Project Structure

```
watchguard-ai/
│
├── main.py               # Main desktop app (Tkinter UI)
├── detector.py           # OpenCV face detection engine
├── media_controller.py   # Pause/play/seek logic (bridge + playerctl + spacebar)
├── browser_bridge.py     # WebSocket server (ws://localhost:8765)
├── session_logger.py     # Away event tracking and log export
├── grok_chat.py          # AI chat module (Groq / Grok / Claude)
├── config.py             # Settings, colours, API key persistence
├── requirements.txt      # Python dependencies
│
└── browser_extension/
    ├── manifest.json     # Chrome Extension Manifest v3
    ├── background.js     # Service worker — WebSocket bridge
    ├── content.js        # Injected into tabs — controls video element
    ├── popup.html        # Extension popup UI
    ├── popup.js          # Popup logic
    └── icon*.png         # Extension icons
```

---

## 🤖 AI Chat

WatchGuard AI includes a built-in AI assistant that knows your current session stats — how long you've been watching, how many times you looked away, your focus score, and more.

Supports three AI backends — you can switch between them in the Settings tab:

| Backend | Model | Speed | Cost |
|---|---|---|---|
| ⚡ **Groq Cloud** | Llama 3.3 70B | Ultra fast | Free tier available |
| 🤖 **Grok (xAI)** | grok-3-latest | Fast | Paid |
| ⬡ **Claude (Anthropic)** | claude-haiku | Fast | Paid |

**To set up:**
1. Open the Settings tab in the app
2. Select your preferred AI backend
3. Paste your API key
4. Click **Save Key**

Get API keys at:
- Groq Cloud: [console.groq.com](https://console.groq.com) ← recommended, has free tier
- xAI Grok: [console.x.ai](https://console.x.ai)
- Anthropic Claude: [console.anthropic.com](https://console.anthropic.com)

> API keys are saved locally to `~/.watchguard_config.json` and never sent anywhere except the selected AI provider.

---

## 🌐 Supported Video Sites

| Site | Pause | Play | Seek |
|---|---|---|---|
| YouTube | ✅ | ✅ | ✅ |
| Netflix | ✅ | ✅ | ✅ |
| Prime Video | ✅ | ✅ | ✅ |
| Disney+ | ✅ | ✅ | ✅ |
| Hotstar | ✅ | ✅ | ✅ |
| Hulu | ✅ | ✅ | ✅ |
| Any HTML5 video | ✅ | ✅ | ✅ |

---

## ⚙️ Configuration

All settings are adjustable from the app's Settings tab:

| Setting | Default | Description |
|---|---|---|
| Check Interval | 500ms | How often the webcam is checked for a face |
| Sensitivity | 0.6 | Face detection confidence (0.1 = lenient, 1.0 = strict) |
| Away Threshold | 5s | Seconds without a face before video is paused |
| AI Backend | Groq Cloud | Which AI provider to use for chat |

---

## 📊 Session Logs

Every watch session is automatically saved to `~/WatchGuard_Logs/`.

Each log captures:
- Session start and end time
- Total number of away events
- Total real time spent away
- For each event: wall clock time, video position when you left, video position when you returned, and duration away

Logs can be exported as **JSON** or **CSV** from the app.

---

## 🛠️ Troubleshooting

**Extension badge shows "OFF"**
→ Make sure `python main.py` is running first. The extension reconnects automatically.

**"No video tab found"**
→ Make sure your video is playing in a browser tab, not a desktop app.

**Video doesn't pause/resume**
→ Check the bridge status indicator in the app — it should show green (Connected).

**Netflix/Prime seek doesn't work on first try**
→ These sites sometimes block `video.currentTime` — click the seek button once manually and it will work on subsequent seeks.

**Webcam not detected**
→ Make sure no other app is using the webcam. Try restarting the app.

**AI Chat returns an error**
→ Double-check your API key in Settings. For Groq Cloud, regenerate the key at [console.groq.com](https://console.groq.com) if you shared it publicly.

---

## 📦 Dependencies

```
opencv-python >= 4.8.0    # Face detection
numpy >= 1.24.0           # Frame processing
pyautogui >= 0.9.54       # Spacebar fallback for media control
Pillow >= 10.0.0          # Image handling
websockets >= 12.0        # Browser bridge WebSocket server
groq                      # Groq Cloud AI SDK
```

---

## 🔒 Privacy

- **All face detection runs locally** on your machine using OpenCV — no images or video are ever sent to any server
- **Webcam feed is never recorded or stored** — only a boolean (face detected: yes/no) is used
- **API keys** are stored only in `~/.watchguard_config.json` on your local machine
- **Session logs** are saved locally to `~/WatchGuard_Logs/` and never uploaded anywhere

---

## 🗺️ Roadmap

- [ ] Multi-face support (detect if someone else is watching)
- [ ] Phone/tablet companion app
- [ ] OBS integration for streamers
- [ ] Scheduled break reminders
- [ ] Weekly watch stats dashboard
- [ ] Firefox extension support

---

## 📄 License

MIT License — feel free to use, modify, and distribute.

---

<div align="center">

Made with ❤️ | WatchGuard AI

*Stop missing the good parts.*

</div>
