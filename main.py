"""
WatchGuard AI v3 — with Grok AI Chatbot
- Browser extension bridge for full position tracking & seek on ANY video
- Works with YouTube, Netflix, Prime Video, Disney+, and local players
- Grok AI chatbot with session context awareness
"""

import tkinter as tk
from tkinter import messagebox
import threading
import time
import sys
from datetime import datetime

try:
    import cv2
    from PIL import Image, ImageTk
except ImportError as e:
    print(f"Missing: {e}\nRun: pip install opencv-python pillow pyautogui")
    sys.exit(1)

from detector         import WatchDetector
from media_controller import MediaController
from browser_bridge   import BrowserBridge
from session_logger   import SessionLogger, fmt_video
from grok_chat        import GrokChat, GrokError
import config


def fmt_time(s):
    s = int(s); h = s//3600; m = (s%3600)//60; s = s%60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Font helpers ──────────────────────────────────────────────────────
FONT_MONO   = ("Courier New", 9)
FONT_MONO_B = ("Courier New", 9, "bold")
FONT_TITLE  = ("Courier New", 16, "bold")
FONT_STAT   = ("Courier New", 17, "bold")
FONT_SMALL  = ("Courier New", 8)
FONT_SMALL_B= ("Courier New", 8, "bold")
FONT_MED    = ("Courier New", 10)
FONT_MED_B  = ("Courier New", 10, "bold")
FONT_BIG_B  = ("Courier New", 11, "bold")

# ── Accent line gradient simulation (thin canvas stripe) ─────────────
def _accent_bar(parent, height=2):
    """Draw a mint→cyan gradient bar using a canvas."""
    c = tk.Canvas(parent, height=height, bg=config.BG_DARK,
                  highlightthickness=0, bd=0)
    c.pack(fill="x")
    def _draw(event=None):
        c.delete("all")
        w = c.winfo_width() or 800
        steps = 60
        for i in range(steps):
            t = i / steps
            # mint (#7cffb2) → cyan (#4fd9ff)
            r = int(0x7c + (0x4f - 0x7c) * t)
            g = int(0xff + (0xd9 - 0xff) * t)
            b = int(0xb2 + (0xff - 0xb2) * t)
            x0 = int(w * i / steps)
            x1 = int(w * (i + 1) / steps) + 1
            c.create_rectangle(x0, 0, x1, height, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
    c.bind("<Configure>", lambda e: _draw())
    c.after(50, _draw)
    return c


class WatchGuardApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("WatchGuard AI")
        self.root.geometry("1200x780")
        self.root.configure(bg=config.BG_DARK)
        self.root.resizable(True, True)
        self.root.minsize(960, 640)

        # State
        self.is_running         = False
        self.mode               = tk.StringVar(value="pause")
        self.current_status     = "idle"
        self.away_start_time    = None
        self.total_away_secs    = 0.0
        self.session_start      = None
        self.interruption_count = 0
        self.timeline_segments  = []
        self._tl_start          = None
        self.last_resume_time   = 0

        # Settings
        self.check_interval  = tk.IntVar(value=config.DEFAULT_CHECK_INTERVAL)
        self.sensitivity     = tk.DoubleVar(value=config.DEFAULT_SENSITIVITY)
        self.away_threshold  = tk.IntVar(value=config.DEFAULT_AWAY_THRESHOLD)

        # Components
        self.bridge     = BrowserBridge()
        self.detector   = WatchDetector()
        self.media_ctrl = MediaController(bridge=self.bridge)
        self.logger     = SessionLogger()
        self.grok       = GrokChat(api_key=config.load_api_key("groq"), backend="groq")
        self._groq_key_cache   = config.load_api_key("groq")
        self._grok_key_cache   = config.load_api_key("grok")
        self._claude_key_cache = config.load_api_key("claude")

        # Camera
        self.camera_frame   = None
        self.camera_running = False
        self.cap            = None

        # Bridge status callback
        self.bridge.on_status_change = self._on_bridge_status

        # Start bridge server
        self.bridge.start()

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ══════════════════════════════════════════════════════════════════
    #  UI
    # ══════════════════════════════════════════════════════════════════

    def build_ui(self):
        # ── Header ───────────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=config.BG_MID, height=56)
        hdr.pack(fill="x"); hdr.pack_propagate(False)

        # left: logo + title
        logo_frame = tk.Frame(hdr, bg=config.BG_MID)
        logo_frame.pack(side="left", padx=18, pady=0, fill="y")
        tk.Label(logo_frame, text="⬡", font=("Courier New", 20, "bold"),
                 bg=config.BG_MID, fg=config.ACCENT).pack(side="left", pady=10)
        title_stack = tk.Frame(logo_frame, bg=config.BG_MID)
        title_stack.pack(side="left", padx=(6, 0), pady=10)
        tk.Label(title_stack, text="WatchGuard AI",
                 font=("Courier New", 13, "bold"),
                 bg=config.BG_MID, fg=config.FG).pack(anchor="w")
        tk.Label(title_stack, text="attention guard · v3",
                 font=FONT_SMALL,
                 bg=config.BG_MID, fg=config.MUTED).pack(anchor="w")

        self.status_badge = tk.Label(
            hdr, text="● IDLE",
            font=FONT_MONO_B,
            bg=config.BG_DARK,
            fg=config.MUTED,
            padx=10, pady=4,
            relief="flat")
        self.status_badge.pack(side="right", padx=18, pady=12)

        # thin rainbow accent bar under header
        _accent_bar(self.root, height=2)

        body = tk.Frame(self.root, bg=config.BG_DARK)
        body.pack(fill="both", expand=True)

        # ── Left sidebar ─────────────────────────────────────────────
        left = tk.Frame(body, bg=config.BG_MID, width=330)
        left.pack(side="left", fill="y"); left.pack_propagate(False)
        self._left(left)

        # thin vertical separator
        tk.Frame(body, bg=config.BG_DARK, width=1).pack(side="left", fill="y")

        # ── Right content: notebook-style tabs ───────────────────────
        right = tk.Frame(body, bg=config.BG_DARK)
        right.pack(side="left", fill="both", expand=True)
        self._right_with_tabs(right)

    # ── Left sidebar ─────────────────────────────────────────────────

    def _left(self, p):
        # Camera section header
        _section_label(p, "CAMERA FEED")

        cf = tk.Frame(p, bg="#000000", height=200)
        cf.pack(padx=12, pady=(0, 8), fill="x")
        cf.pack_propagate(False)
        self.cam_label = tk.Label(cf, bg="#000000")
        self.cam_label.pack(fill="both", expand=True)

        self.face_lbl = tk.Label(p, text="○  No face detected",
                                  font=FONT_SMALL,
                                  bg=config.BG_MID, fg=config.MUTED)
        self.face_lbl.pack(pady=(0, 6))

        _sep(p)

        # Browser bridge section
        _section_label(p, "BROWSER BRIDGE")

        bf = tk.Frame(p, bg=config.BG_MID)
        bf.pack(padx=12, pady=(0, 4), fill="x")

        self.bridge_dot = tk.Label(bf, text="○  Waiting for extension...",
                                    font=FONT_SMALL,
                                    bg=config.BG_MID, fg=config.MUTED)
        self.bridge_dot.pack(anchor="w")

        self.video_pos_lbl = tk.Label(bf, text="Video: --:--  /  --:--",
                                       font=FONT_MED_B,
                                       bg=config.BG_MID, fg=config.ACCENT)
        self.video_pos_lbl.pack(anchor="w", pady=(4, 2))

        self.page_lbl = tk.Label(bf, text="",
                                  font=FONT_SMALL,
                                  bg=config.BG_MID, fg=config.MUTED,
                                  wraplength=290, justify="left")
        self.page_lbl.pack(anchor="w", pady=(0, 4))

        install_f = tk.Frame(bf, bg=config.BG_MID)
        install_f.pack(fill="x", pady=(4, 0))
        tk.Label(install_f,
                 text="Install the browser extension\nthen load YouTube/Netflix — bridge auto-connects.",
                 font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED,
                 justify="left").pack(anchor="w")

        _sep(p)

        # Mode section
        _section_label(p, "MODE")

        mf = tk.Frame(p, bg=config.BG_MID)
        mf.pack(padx=12, pady=(0, 4), fill="x")

        for val, lbl, desc in [
            ("pause", "⏸  Auto-Pause",    "Pauses & resumes automatically"),
            ("log",   "📋  Timestamp Log", "Records missed moments for replay"),
        ]:
            f = tk.Frame(mf, bg=config.BG_MID); f.pack(fill="x", pady=2)
            tk.Radiobutton(f, text=lbl, variable=self.mode, value=val,
                           font=FONT_MED_B,
                           bg=config.BG_MID, fg=config.FG,
                           selectcolor=config.BG_DARK,
                           activebackground=config.BG_MID,
                           command=self._mode_changed).pack(anchor="w")
            tk.Label(f, text=f"    {desc}", font=FONT_SMALL,
                     bg=config.BG_MID, fg=config.MUTED).pack(anchor="w")

        _sep(p)

        # Settings sliders
        _section_label(p, "SETTINGS")

        sf = tk.Frame(p, bg=config.BG_MID)
        sf.pack(padx=12, pady=(0, 4), fill="x")
        self._slider(sf, "Away threshold (s)", self.away_threshold, 2, 30)
        self._slider(sf, "Check interval (ms)", self.check_interval, 200, 2000)
        self._slider(sf, "Sensitivity", self.sensitivity, 0.1, 1.0, True)

        _sep(p)

        # Buttons
        bf2 = tk.Frame(p, bg=config.BG_MID)
        bf2.pack(padx=12, pady=8, fill="x")

        self.start_btn = tk.Button(
            bf2, text="▶  START GUARD",
            font=FONT_BIG_B,
            bg=config.ACCENT, fg=config.BG_DARK,
            relief="flat", cursor="hand2",
            activebackground="#5de89e",
            activeforeground=config.BG_DARK,
            command=self.toggle_guard, height=2)
        self.start_btn.pack(fill="x", pady=(0, 6))

        tk.Button(bf2, text="💾  Export CSV",
                  font=FONT_SMALL, bg=config.BG_DARK, fg=config.MUTED,
                  relief="flat", cursor="hand2",
                  activebackground=config.BG_MID,
                  command=self.export_log).pack(fill="x")

    # ── Right panel with manual tabs ─────────────────────────────────

    def _right_with_tabs(self, p):
        # Tab bar
        tab_bar = tk.Frame(p, bg=config.BG_MID, height=40)
        tab_bar.pack(fill="x"); tab_bar.pack_propagate(False)

        self._tab_frames  = {}
        self._tab_buttons = {}
        self._active_tab  = tk.StringVar(value="dashboard")

        tab_defs = [
            ("dashboard", "📊  Dashboard"),
            ("chat",      "🤖  AI Chat"),
            ("settings",  "⚙  Settings"),
        ]

        for key, label in tab_defs:
            btn = tk.Button(
                tab_bar, text=label,
                font=FONT_SMALL_B,
                relief="flat", cursor="hand2", padx=16,
                command=lambda k=key: self._show_tab(k))
            btn.pack(side="left", fill="y")
            self._tab_buttons[key] = btn

        # thin accent line under tab bar
        _accent_bar(p, height=1)

        # Content area
        content = tk.Frame(p, bg=config.BG_DARK)
        content.pack(fill="both", expand=True)

        # Build each tab frame
        dash_frame = tk.Frame(content, bg=config.BG_DARK)
        self._tab_frames["dashboard"] = dash_frame
        self._build_dashboard(dash_frame)

        chat_frame = tk.Frame(content, bg=config.BG_DARK)
        self._tab_frames["chat"] = chat_frame
        self._build_chat(chat_frame)

        sett_frame = tk.Frame(content, bg=config.BG_DARK)
        self._tab_frames["settings"] = sett_frame
        self._build_settings_tab(sett_frame)

        self._show_tab("dashboard")

    def _show_tab(self, key):
        for k, f in self._tab_frames.items():
            f.pack_forget()
        self._tab_frames[key].pack(fill="both", expand=True)
        self._active_tab.set(key)
        for k, btn in self._tab_buttons.items():
            active = (k == key)
            btn.config(
                bg=config.BG_DARK if active else config.BG_MID,
                fg=config.ACCENT  if active else config.MUTED,
                relief="flat")

    # ── Dashboard tab ─────────────────────────────────────────────────

    def _build_dashboard(self, p):
        # Stat cards row
        sr = tk.Frame(p, bg=config.BG_DARK)
        sr.pack(fill="x", padx=16, pady=14)
        self.stat_session = self._stat(sr, "SESSION", "00:00:00")
        self.stat_away    = self._stat(sr, "AWAY",    "00:00:00")
        self.stat_pauses  = self._stat(sr, "BREAKS",  "0")
        self.stat_score   = self._stat(sr, "FOCUS",   "100%")

        # Timeline
        tf = tk.Frame(p, bg=config.BG_MID, bd=0)
        tf.pack(fill="x", padx=16, pady=(0, 10))
        _section_label_inline(tf, "ATTENTION TIMELINE")
        self.tl_canvas = tk.Canvas(tf, bg=config.BG_DARK, height=22,
                                    highlightthickness=0)
        self.tl_canvas.pack(fill="x", padx=10, pady=(0, 8))

        # Resume panel (log mode only)
        self.resume_outer = tk.Frame(p, bg=config.BG_MID)

        rh = tk.Frame(self.resume_outer, bg=config.BG_MID)
        rh.pack(fill="x")
        tk.Label(rh, text="📋  MISSED MOMENTS",
                 font=FONT_SMALL_B,
                 bg=config.BG_MID, fg=config.MUTED).pack(side="left", padx=10, pady=8)
        tk.Label(rh, text="auto-seeks on return  ·  ▶ to replay",
                 font=FONT_SMALL,
                 bg=config.BG_MID, fg=config.MUTED).pack(side="right", padx=10)

        self.resume_canvas = tk.Canvas(self.resume_outer, bg=config.BG_MID,
                                        height=130, highlightthickness=0)
        rsb = tk.Scrollbar(self.resume_outer, orient="vertical",
                           command=self.resume_canvas.yview)
        self.resume_canvas.configure(yscrollcommand=rsb.set)
        rsb.pack(side="right", fill="y")
        self.resume_canvas.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.resume_list = tk.Frame(self.resume_canvas, bg=config.BG_MID)
        self.resume_canvas.create_window((0, 0), window=self.resume_list, anchor="nw")
        self.resume_list.bind("<Configure>",
            lambda e: self.resume_canvas.configure(
                scrollregion=self.resume_canvas.bbox("all")))
        self._no_events_lbl = tk.Label(self.resume_list,
                                        text="No away events yet.",
                                        font=FONT_MONO,
                                        bg=config.BG_MID, fg=config.MUTED)
        self._no_events_lbl.pack(anchor="w", padx=8, pady=6)

        # Event log
        lf = tk.Frame(p, bg=config.BG_MID)
        lf.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        lh = tk.Frame(lf, bg=config.BG_MID)
        lh.pack(fill="x")
        tk.Label(lh, text="EVENT LOG", font=FONT_SMALL_B,
                 bg=config.BG_MID, fg=config.MUTED).pack(side="left", padx=10, pady=8)
        tk.Button(lh, text="✕ Clear", font=FONT_SMALL,
                  bg=config.BG_MID, fg=config.MUTED,
                  relief="flat", cursor="hand2",
                  activebackground=config.BG_DARK,
                  command=self._clear_log).pack(side="right", padx=10)

        self.log_text = tk.Text(lf, bg=config.BG_DARK, fg=config.FG,
                                 font=FONT_MONO, relief="flat",
                                 state="disabled", wrap="word",
                                 padx=10, pady=8,
                                 selectbackground=config.BG_MID,
                                 insertbackground=config.ACCENT)
        sb = tk.Scrollbar(lf, command=self.log_text.yview,
                          bg=config.BG_MID, troughcolor=config.BG_DARK)
        self.log_text.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True, padx=(10, 0), pady=(0, 10))

        for tag, col in [("watching", config.GREEN), ("away", config.RED),
                          ("resume", config.YELLOW), ("info", config.MUTED),
                          ("system", config.ACCENT)]:
            self.log_text.tag_config(tag, foreground=col)

        self._log("system", "WatchGuard AI ready.")
        self._log("info",   "Install the browser extension → open YouTube/Netflix → bridge auto-connects.")

    # ── Chat tab ─────────────────────────────────────────────────────

    def _build_chat(self, p):
        # Header row
        hdr = tk.Frame(p, bg=config.BG_MID)
        hdr.pack(fill="x", padx=16, pady=(12, 0))

        backend_name = "Grok (xAI)" if self.grok.is_grok() else "Claude (Anthropic)"
        self.chat_header_lbl = tk.Label(
            hdr, text=f"🤖  {backend_name.upper()} ASSISTANT",
            font=FONT_MED_B,
            bg=config.BG_MID, fg=config.ACCENT)
        self.chat_header_lbl.pack(side="left", padx=10, pady=8)

        self.chat_key_indicator = tk.Label(
            hdr,
            text="● Key set" if self.grok.api_key else "○ No key — go to Settings",
            font=FONT_SMALL,
            bg=config.BG_MID,
            fg=config.GREEN if self.grok.api_key else config.MUTED)
        self.chat_key_indicator.pack(side="right", padx=10)

        tk.Button(hdr, text="✕ Clear chat",
                  font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED,
                  relief="flat", cursor="hand2",
                  activebackground=config.BG_DARK,
                  command=self._clear_chat).pack(side="right")

        # Chat display
        chat_outer = tk.Frame(p, bg=config.BG_MID)
        chat_outer.pack(fill="both", expand=True, padx=16, pady=8)

        self.chat_text = tk.Text(
            chat_outer, bg=config.BG_DARK, fg=config.FG,
            font=FONT_MONO, relief="flat",
            state="disabled", wrap="word", padx=12, pady=10,
            cursor="arrow",
            selectbackground=config.BG_MID,
            insertbackground=config.ACCENT)
        csb = tk.Scrollbar(chat_outer, command=self.chat_text.yview,
                           bg=config.BG_MID, troughcolor=config.BG_DARK)
        self.chat_text.config(yscrollcommand=csb.set)
        csb.pack(side="right", fill="y")
        self.chat_text.pack(fill="both", expand=True)

        self.chat_text.tag_config("you",    foreground=config.ACCENT,  font=FONT_MONO_B)
        self.chat_text.tag_config("bot",    foreground=config.FG)
        self.chat_text.tag_config("sys",    foreground=config.MUTED,   font=("Courier New", 8, "italic"))
        self.chat_text.tag_config("err",    foreground=config.RED)
        self.chat_text.tag_config("typing", foreground=config.YELLOW,  font=("Courier New", 8, "italic"))

        # Input row
        inp_frame = tk.Frame(p, bg=config.BG_DARK)
        inp_frame.pack(fill="x", padx=16, pady=(0, 12))

        self.chat_input = tk.Text(
            inp_frame, bg=config.BG_MID, fg=config.FG,
            font=FONT_MED, relief="flat",
            height=3, padx=10, pady=8, wrap="word",
            insertbackground=config.ACCENT,
            selectbackground=config.BG_DARK)
        self.chat_input.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.chat_input.bind("<Return>",       self._on_chat_enter)
        self.chat_input.bind("<Shift-Return>", lambda e: None)

        send_btn = tk.Button(
            inp_frame, text="Send\n▶",
            font=FONT_MONO_B,
            bg=config.ACCENT, fg=config.BG_DARK,
            relief="flat", cursor="hand2", width=6,
            activebackground="#5de89e",
            activeforeground=config.BG_DARK,
            command=self._send_chat)
        send_btn.pack(side="left", fill="y")

        # Quick-prompt buttons
        qp_frame = tk.Frame(p, bg=config.BG_DARK)
        qp_frame.pack(fill="x", padx=16, pady=(0, 8))
        quick_prompts = [
            ("📊 My stats",    "Give me a summary of my watch session stats."),
            ("💡 Tips",        "Give me tips to stay more focused while watching."),
            ("❓ How it works","Explain how WatchGuard AI detects if I'm watching."),
        ]
        for label, prompt in quick_prompts:
            tk.Button(
                qp_frame, text=label,
                font=FONT_SMALL, bg=config.BG_MID, fg=config.FG,
                relief="flat", cursor="hand2", padx=8,
                activebackground=config.BG_DARK,
                command=lambda pr=prompt: self._send_quick(pr)
            ).pack(side="left", padx=(0, 6), pady=2)

        self._chat_append("sys", "WatchGuard Assistant powered by Grok. Ask me anything!\n"
                                  "Set your xAI API key in the ⚙ Settings tab to get started.\n")

    # ── Settings tab ─────────────────────────────────────────────────

    def _build_settings_tab(self, p):
        tk.Label(p, text="⚙  SETTINGS",
                 font=FONT_MED_B,
                 bg=config.BG_DARK, fg=config.ACCENT).pack(anchor="w", padx=20, pady=(16, 8))

        # Backend selector
        be_frame = tk.Frame(p, bg=config.BG_MID)
        be_frame.pack(fill="x", padx=16, pady=(0, 8))
        _section_label_inline(be_frame, "AI BACKEND")

        self.backend_var = tk.StringVar(value=self.grok.backend)
        be_row = tk.Frame(be_frame, bg=config.BG_MID)
        be_row.pack(fill="x", padx=12, pady=(0, 10))
        for val, lbl, hint in [
            ("groq",   "⚡ Groq Cloud (Llama 3.3)", "console.groq.com — free tier available"),
            ("grok",   "🤖 Grok  (xAI)",             "console.x.ai"),
            ("claude", "⬡ Claude (Anthropic)",       "console.anthropic.com"),
        ]:
            rb = tk.Radiobutton(be_row, text=f"{lbl}   key from {hint}",
                                variable=self.backend_var, value=val,
                                font=FONT_MONO,
                                bg=config.BG_MID, fg=config.FG,
                                selectcolor=config.BG_DARK,
                                activebackground=config.BG_MID,
                                command=self._on_backend_change)
            rb.pack(anchor="w", pady=2)

        # API Key section
        api_frame = tk.Frame(p, bg=config.BG_MID)
        api_frame.pack(fill="x", padx=16, pady=(0, 12))

        self.key_label = tk.Label(api_frame, text="API KEY",
                 font=FONT_SMALL_B,
                 bg=config.BG_MID, fg=config.MUTED)
        self.key_label.pack(anchor="w", padx=12, pady=(12, 4))

        key_row = tk.Frame(api_frame, bg=config.BG_MID)
        key_row.pack(fill="x", padx=12, pady=(0, 6))

        self.api_key_var = tk.StringVar(value=self.grok.api_key)
        self.key_entry = tk.Entry(
            key_row, textvariable=self.api_key_var,
            font=FONT_MED, bg=config.BG_DARK, fg=config.FG,
            relief="flat", show="•",
            insertbackground=config.ACCENT,
            selectbackground=config.BG_MID)
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

        self.show_key_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            key_row, text="Show",
            variable=self.show_key_var,
            font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED,
            selectcolor=config.BG_DARK, activebackground=config.BG_MID,
            command=self._toggle_key_visibility).pack(side="left")

        btn_row = tk.Frame(api_frame, bg=config.BG_MID)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        tk.Button(
            btn_row, text="💾  Save Key",
            font=FONT_MONO_B,
            bg=config.ACCENT, fg=config.BG_DARK,
            relief="flat", cursor="hand2",
            activebackground="#5de89e",
            command=self._save_api_key).pack(side="left", padx=(0, 8), ipady=4)

        tk.Button(
            btn_row, text="🗑  Clear Key",
            font=FONT_MONO,
            bg=config.BG_DARK, fg=config.MUTED,
            relief="flat", cursor="hand2",
            activebackground=config.BG_MID,
            command=self._clear_api_key).pack(side="left", ipady=4)

        self.key_status_lbl = tk.Label(
            api_frame,
            text=self._key_status_text(),
            font=FONT_MONO,
            bg=config.BG_MID,
            fg=config.GREEN if self.grok.api_key else config.MUTED)
        self.key_status_lbl.pack(anchor="w", padx=12, pady=(0, 12))

        _sep(p)

        # Info block
        info_frame = tk.Frame(p, bg=config.BG_MID)
        info_frame.pack(fill="x", padx=16, pady=(0, 12))
        _section_label_inline(info_frame, "ABOUT AI CHAT")
        info_text = (
            "The AI chat knows your current session stats — focus score,\n"
            "away time, breaks, and what you're watching.\n\n"
            "Groq Cloud: llama-3.3-70b  (free tier — console.groq.com)\n"
            "Grok model: grok-3-latest  (requires xAI credits)\n"
            "Claude:     claude-haiku   (fast & cheap)\n\n"
            "Your keys are saved locally in ~/.watchguard_config.json\n"
            "and are only sent to the selected AI provider."
        )
        tk.Label(info_frame, text=info_text,
                 font=FONT_SMALL,
                 bg=config.BG_MID, fg=config.MUTED,
                 justify="left").pack(anchor="w", padx=12, pady=(0, 12))

    # ── Chat helpers ──────────────────────────────────────────────────

    def _chat_append(self, tag, text):
        self.chat_text.config(state="normal")
        self.chat_text.insert("end", text, tag)
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _clear_chat(self):
        self.grok.clear_history()
        self.chat_text.config(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.config(state="disabled")
        self._chat_append("sys", "Chat cleared.\n")

    def _on_chat_enter(self, event):
        if not event.state & 0x1:
            self._send_chat()
            return "break"

    def _send_quick(self, prompt):
        self.chat_input.delete("1.0", "end")
        self.chat_input.insert("end", prompt)
        self._send_chat()

    def _send_chat(self):
        msg = self.chat_input.get("1.0", "end").strip()
        if not msg:
            return
        self.chat_input.delete("1.0", "end")
        self._chat_append("you", f"\nYou: {msg}\n")
        ctx = self._build_session_context()
        self.grok.set_session_context(ctx)
        self.chat_text.config(state="normal")
        self._thinking_mark = self.chat_text.index("end-1c")
        self.chat_text.config(state="disabled")
        thinking_label = "Grok is thinking…" if self.grok.is_grok() else "Claude is thinking…"
        self._chat_append("typing", f"{thinking_label}\n")
        threading.Thread(target=self._do_grok_call, args=(msg,), daemon=True).start()

    def _remove_thinking_line(self):
        try:
            self.chat_text.config(state="normal")
            start = self._thinking_mark + "+1c"
            end = self.chat_text.index(f"{start} lineend+1c")
            self.chat_text.delete(start, end)
        except Exception:
            pass
        finally:
            self.chat_text.config(state="disabled")

    def _do_grok_call(self, msg):
        try:
            reply = self.grok.send(msg)
            bot_name = "Grok" if self.grok.is_grok() else "Claude"
            self.root.after(0, lambda r=reply, n=bot_name: (
                self._remove_thinking_line(),
                self._chat_append("bot", f"{n}: {r}\n")
            ))
        except GrokError as e:
            err_msg = str(e)
            print(f"[Grok] GrokError: {err_msg}")
            self.root.after(0, lambda m=err_msg: (
                self._remove_thinking_line(),
                self._chat_append("err", f"⚠ Error: {m}\n")
            ))
        except Exception as e:
            err_msg = str(e)
            print(f"[Grok] Unexpected error: {err_msg}")
            import traceback; traceback.print_exc()
            self.root.after(0, lambda m=err_msg: (
                self._remove_thinking_line(),
                self._chat_append("err", f"⚠ Unexpected error: {m}\n")
            ))

    def _build_session_context(self):
        if not self.session_start:
            return "No active session — guard has not been started yet."
        elapsed  = time.time() - self.session_start
        away     = self.total_away_secs + (
            time.time() - self.away_start_time
            if self.current_status == "away" and self.away_start_time else 0)
        focus    = max(0, 100 - int((away / max(elapsed, 1)) * 100))
        pos, dur = (None, None)
        if self.bridge.is_connected():
            pos, dur = self.bridge.get_position()
        page = self.bridge.get_page_title() if self.bridge.is_connected() else ""
        lines = [
            f"Session duration: {fmt_time(elapsed)}",
            f"Total away time:  {fmt_time(away)}",
            f"Break count:      {self.interruption_count}",
            f"Focus score:      {focus}%",
            f"Current status:   {self.current_status}",
            f"Mode:             {'Auto-Pause' if self.mode.get() == 'pause' else 'Timestamp Log'}",
        ]
        if pos is not None:
            lines.append(f"Video position:   {fmt_video(pos)} / {fmt_video(dur)}")
        if page:
            lines.append(f"Watching:         {page}")
        return "\n".join(lines)

    # ── Settings tab helpers ──────────────────────────────────────────

    def _on_backend_change(self):
        backend = self.backend_var.get()
        self.grok.set_backend(backend)
        name = self.grok.backend_display_name()
        saved_key = {
            "groq":   self._groq_key_cache,
            "grok":   self._grok_key_cache,
            "claude": self._claude_key_cache,
        }.get(backend, "")
        self.api_key_var.set(saved_key)
        self.grok.set_api_key(saved_key)
        self.key_status_lbl.config(
            text=self._key_status_text(),
            fg=config.GREEN if saved_key else config.MUTED)
        self.chat_key_indicator.config(
            text=f"● {name} — key set" if saved_key else f"○ {name} — no key — go to Settings",
            fg=config.GREEN if saved_key else config.MUTED)
        self.chat_header_lbl.config(text=f"🤖  {name.upper()} ASSISTANT")
        self.grok.clear_history()
        self._chat_append("sys", f"Switched to {name}. Chat history cleared.\n")

    def _key_status_text(self):
        if self.grok.api_key:
            masked = self.grok.api_key[:6] + "•" * max(0, len(self.grok.api_key) - 6)
            return f"✓ Key saved: {masked}"
        return "○ No API key set"

    def _toggle_key_visibility(self):
        self.key_entry.config(show="" if self.show_key_var.get() else "•")

    def _save_api_key(self):
        key = self.api_key_var.get().strip()
        backend = self.backend_var.get()
        backend_name = self.grok.backend_display_name()
        if not key:
            messagebox.showwarning("Empty Key", f"Please enter your {backend_name} API key first.")
            return
        self.grok.set_api_key(key)
        config.save_api_key(key, backend)
        if backend == "groq":
            self._groq_key_cache = key
        elif backend == "grok":
            self._grok_key_cache = key
        else:
            self._claude_key_cache = key
        self.key_status_lbl.config(text=self._key_status_text(), fg=config.GREEN)
        self.chat_key_indicator.config(text=f"● {backend_name} key set", fg=config.GREEN)
        messagebox.showinfo("Saved", f"{backend_name} API key saved!\nYou can now use the AI Chat tab.")

    def _clear_api_key(self):
        backend = self.backend_var.get()
        backend_name = self.grok.backend_display_name()
        self.api_key_var.set("")
        self.grok.set_api_key("")
        config.save_api_key("", backend)
        if backend == "groq":
            self._groq_key_cache = ""
        elif backend == "grok":
            self._grok_key_cache = ""
        else:
            self._claude_key_cache = ""
        self.key_status_lbl.config(text=self._key_status_text(), fg=config.MUTED)
        self.chat_key_indicator.config(
            text=f"○ {backend_name} — no key — go to Settings", fg=config.MUTED)

    # ── Helpers ───────────────────────────────────────────────────────

    def _slider(self, p, lbl, var, lo, hi, fl=False):
        f = tk.Frame(p, bg=p.cget("bg")); f.pack(fill="x", pady=3)
        row = tk.Frame(f, bg=p.cget("bg")); row.pack(fill="x")
        tk.Label(row, text=lbl, font=FONT_SMALL,
                 bg=p.cget("bg"), fg=config.FG).pack(side="left")
        vl = tk.Label(row, font=FONT_SMALL_B,
                       bg=p.cget("bg"), fg=config.ACCENT)
        vl.pack(side="right")
        def upd(v): vl.config(text=f"{float(v):.1f}" if fl else str(int(float(v))))
        tk.Scale(f, variable=var, from_=lo, to=hi, orient="horizontal",
                 bg=p.cget("bg"), fg=config.FG,
                 troughcolor=config.BG_DARK,
                 highlightthickness=0, showvalue=False,
                 command=upd,
                 resolution=0.1 if fl else 1).pack(fill="x")
        upd(var.get())

    def _stat(self, p, title, init):
        f = tk.Frame(p, bg=config.BG_MID, padx=10, pady=10)
        f.pack(side="left", fill="both", expand=True, padx=(0, 8))
        # thin top accent line
        tk.Frame(f, bg=config.ACCENT, height=2).pack(fill="x", pady=(0, 6))
        tk.Label(f, text=title, font=("Courier New", 7, "bold"),
                 bg=config.BG_MID, fg=config.MUTED).pack(anchor="w")
        lbl = tk.Label(f, text=init, font=FONT_STAT,
                       bg=config.BG_MID, fg=config.FG)
        lbl.pack(anchor="w")
        return lbl

    # ══════════════════════════════════════════════════════════════════
    #  GUARD LOGIC
    # ══════════════════════════════════════════════════════════════════

    def toggle_guard(self):
        self._stop_guard() if self.is_running else self._start_guard()

    def _start_guard(self):
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened():
            messagebox.showerror("Camera Error",
                "Could not open webcam. Check permissions.")
            return
        self.is_running         = True
        self.session_start      = time.time()
        self.total_away_secs    = 0.0
        self.interruption_count = 0
        self.timeline_segments  = []
        self._tl_start          = time.time()
        self.camera_running     = True
        self.logger             = SessionLogger()
        self.cam_label.config(height=300)
        self.start_btn.config(text="⏹  STOP GUARD", bg=config.RED, fg="white",
                              activebackground="#cc4460")
        mode = "AUTO-PAUSE" if self.mode.get() == "pause" else "TIMESTAMP LOGGER"
        self._log("system", f"▶ Started — {mode}")
        threading.Thread(target=self._cam_loop,    daemon=True).start()
        threading.Thread(target=self._detect_loop, daemon=True).start()
        threading.Thread(target=self._pos_loop,    daemon=True).start()
        self._stats_loop()

    def _stop_guard(self):
        self.is_running = self.camera_running = False
        self.cam_label.config(height=170)
        if self.cap: self.cap.release(); self.cap = None
        if self.current_status == "away" and self.mode.get() == "pause":
            self.media_ctrl.play()
        elapsed = time.time() - self.session_start if self.session_start else 0
        focus   = max(0, 100 - int((self.total_away_secs / max(elapsed, 1)) * 100))
        self._update_status("idle")
        self.start_btn.config(text="▶  START GUARD", bg=config.ACCENT,
                              fg=config.BG_DARK, activebackground="#5de89e")
        self._log("system",
            f"⏹ Done — {fmt_time(elapsed)} | Focus {focus}% | {self.interruption_count} break(s)")
        if self.logger.away_events:
            path = self.logger.save_session()
            self._log("info", f"📄 Saved → {path}")

    # ── Loops ─────────────────────────────────────────────────────────

    def _cam_loop(self):
        while self.camera_running and self.cap:
            ret, frame = self.cap.read()
            if not ret: continue
            ann  = self.detector.annotate_frame(frame)
            disp = cv2.resize(ann, (360, 220) if self.is_running else (280, 160))
            rgb  = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
            img  = Image.fromarray(rgb)
            imgtk = ImageTk.PhotoImage(image=img)
            self.camera_frame = frame.copy()
            self.cam_label.configure(image=imgtk)
            self.cam_label.image = imgtk
            time.sleep(0.05)

    def _pos_loop(self):
        while self.is_running:
            if self.bridge.is_connected():
                pos, dur = self.bridge.get_position()
                if pos is not None:
                    title = self.bridge.get_page_title()
                    short = (title[:38] + "…") if len(title) > 40 else title
                    self.root.after(0, lambda p=pos, d=dur, t=short: (
                        self.video_pos_lbl.config(
                            text=f"Video: {fmt_video(p)}  /  {fmt_video(d)}"),
                        self.page_lbl.config(text=t)
                    ))
            time.sleep(1.0)

    def _detect_loop(self):
        cons_away = cons_watch = 0
        was_away = False
        while self.is_running:
            t0 = time.time()
            ms = self.check_interval.get()
            if self.camera_frame is not None:
                watching = self.detector.is_watching(
                    self.camera_frame, sensitivity=self.sensitivity.get())
                if watching:
                    cons_away = 0; cons_watch += 1
                    self.root.after(0, lambda: self.face_lbl.config(
                        text="●  Watching", fg=config.GREEN))
                    if was_away and cons_watch >= 2:
                        was_away = False
                        real_away = time.time() - self.away_start_time
                        self.total_away_secs += real_away
                        self.root.after(0, self._on_returned, real_away)
                else:
                    cons_watch = 0; cons_away += 1
                    self.root.after(0, lambda: self.face_lbl.config(
                        text="○  Away", fg=config.RED))
                    thresh = max(1, int(self.away_threshold.get() * 1000 / ms))
                    if cons_away >= thresh and not was_away:
                        was_away = True
                        self.away_start_time = time.time()
                        self.root.after(0, self._on_away)
            time.sleep(max(0, ms / 1000 - (time.time() - t0)))

    # ── Away / Return ─────────────────────────────────────────────────

    def _on_away(self):
        self._update_status("away")
        self.interruption_count += 1
        ts  = datetime.now().strftime("%H:%M:%S")
        pos = None
        if self.bridge.is_connected():
            for _ in range(5):
                pos, _ = self.bridge.get_position()
                if pos is not None: break
                time.sleep(0.2)
        print("🔥 FINAL AWAY POSITION:", pos)
        if self.mode.get() == "pause":
            self.media_ctrl.pause()
            tag   = "AUTO-PAUSE" if self.bridge.is_connected() else "SPACEBAR"
            pos_s = f" at {fmt_video(pos)}" if pos is not None else ""
            self._log("away", f"[{ts}] 👤 Away — {tag} PAUSED{pos_s}")
        else:
            pos_s = fmt_video(pos) if pos is not None else "unknown"
            self._log("away", f"[{ts}] 👤 Away — video was at {pos_s}")
        self.logger.log_away(pos, self.away_start_time)
        self._add_tl("away")

    def _on_returned(self, real_away_secs):
        self._update_status("watching")
        ts  = datetime.now().strftime("%H:%M:%S")
        pos = self.media_ctrl.get_position()
        closed = self.logger.log_return(pos)

        if self.mode.get() == "pause":
            # ── Auto-Pause mode: unchanged — just resume in place ────
            self.media_ctrl.play()
            self._log("resume",
                f"[{ts}] ▶ Returned — resumed ({fmt_time(real_away_secs)} away)")

        else:
            # ── Timestamp Logger mode: auto-seek to missed moment ────
            last_event = self.logger.get_last_event()
            if last_event and last_event.video_pos_away is not None:
                jump_to = max(0, last_event.video_pos_away - 2)
                self._log("resume",
                    f"[{ts}] ↩ Face detected — auto-jumping to {fmt_video(jump_to)} "
                    f"({fmt_time(real_away_secs)} away)")
                if time.time() - self.last_resume_time > 2:
                    self.last_resume_time = time.time()
                    threading.Thread(
                        target=self._do_jump, args=(jump_to,), daemon=True).start()
            else:
                self._log("resume",
                    f"[{ts}] ↩ Back after {fmt_time(real_away_secs)} — no position to seek")

        self._add_tl("watching")
        if closed and self.mode.get() == "log":
            self.root.after(0, self._refresh_resume)

    # ── Resume panel ──────────────────────────────────────────────────

    def _refresh_resume(self):
        for w in self.resume_list.winfo_children():
            w.destroy()
        events = self.logger.get_resume_events()
        if not events:
            tk.Label(self.resume_list, text="No away events yet.",
                     font=FONT_MONO,
                     bg=config.BG_MID, fg=config.MUTED).pack(anchor="w", padx=8, pady=6)
            return
        for ev in events:
            row = tk.Frame(self.resume_list, bg=config.BG_DARK)
            row.pack(fill="x", pady=2, padx=4)
            info = tk.Frame(row, bg=config.BG_DARK)
            info.pack(side="left", fill="x", expand=True, padx=6, pady=4)
            tk.Label(info, text=f"#{ev.event_id}  Left at {fmt_video(ev.video_pos_away)}",
                     font=FONT_MONO_B,
                     bg=config.BG_DARK, fg=config.FG).pack(anchor="w")
            away_s = fmt_time(ev.away_duration) if ev.away_duration else "?"
            tk.Label(info, text=f"   Gone {away_s}  →  return point {fmt_video(ev.video_pos_return)}",
                     font=FONT_SMALL,
                     bg=config.BG_DARK, fg=config.MUTED).pack(anchor="w")
            # "auto-sought" badge — shows the seek already happened automatically
            tk.Label(info, text="   ✓ auto-sought on return",
                     font=FONT_SMALL,
                     bg=config.BG_DARK, fg=config.ACCENT).pack(anchor="w")
            jp = ev.video_pos_away
            if jp is not None:
                can_seek = self.bridge.is_connected()
                tk.Button(
                    row, text=f"↩ {fmt_video(jp)}",
                    font=FONT_MONO_B,
                    bg=config.ACCENT if can_seek else config.MUTED,
                    fg=config.BG_DARK, relief="flat", cursor="hand2",
                    activebackground="#5de89e",
                    command=lambda p=jp: self._jump_to(p)
                ).pack(side="right", padx=6, pady=4)

    def _jump_to(self, pos_seconds: float):
        if not self.bridge.is_connected():
            messagebox.showinfo("Browser Extension Required",
                f"Install the WatchGuard browser extension to enable auto-seek.\n\n"
                f"Manually seek to: {fmt_video(pos_seconds)}")
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self._log("resume", f"[{ts}] ⏩ Jumping to {fmt_video(pos_seconds)}...")
        threading.Thread(target=self._do_jump, args=(pos_seconds,), daemon=True).start()

    def _do_jump(self, pos):
        ok = self.media_ctrl.play_from(pos)
        ts = datetime.now().strftime("%H:%M:%S")
        if not ok:
            self.media_ctrl.play()
            msg = f"[{ts}] ⚠ Seek failed → forced PLAY"
        else:
            msg = f"[{ts}] ✓ Jumped to {fmt_video(pos)}"
        self.root.after(0, lambda: self._log("resume" if ok else "away", msg))
        print(f"[DEBUG] Sending seek to {pos}")

    # ── Bridge status callback ─────────────────────────────────────────

    def _on_bridge_status(self, connected: bool):
        def _upd():
            if connected:
                self.bridge_dot.config(
                    text="●  Browser extension connected", fg=config.GREEN)
                self._log("system", "✓ Browser bridge connected — full seek enabled")
            else:
                self.bridge_dot.config(
                    text="○  Waiting for extension...", fg=config.MUTED)
                self.video_pos_lbl.config(text="Video: --:--  /  --:--")
                self.page_lbl.config(text="")
        self.root.after(0, _upd)

    # ── Stats loop ────────────────────────────────────────────────────

    def _stats_loop(self):
        if not self.is_running: return
        if self.session_start:
            el = time.time() - self.session_start
            self.stat_session.config(text=fmt_time(el))
            away = self.total_away_secs + (
                time.time() - self.away_start_time
                if self.current_status == "away" and self.away_start_time else 0)
            self.stat_away.config(text=fmt_time(away))
            self.stat_pauses.config(text=str(self.interruption_count))
            focus = max(0, 100 - int((away / max(el, 1)) * 100))
            col = config.GREEN if focus >= 70 else config.YELLOW if focus >= 40 else config.RED
            self.stat_score.config(text=f"{focus}%", fg=col)
        self.root.after(1000, self._stats_loop)

    # ── Timeline ──────────────────────────────────────────────────────

    def _add_tl(self, status):
        now = time.time()
        dur = now - (self._tl_start or now)
        self._tl_start = now
        self.timeline_segments.append((status, max(dur, 0.5)))
        self._draw_tl()

    def _draw_tl(self):
        c = self.tl_canvas; c.delete("all")
        if not self.timeline_segments: return
        total = sum(d for _, d in self.timeline_segments) or 1
        w = c.winfo_width() or 500; x = 0
        for status, dur in self.timeline_segments:
            sw = max(2, (dur / total) * w)
            c.create_rectangle(x, 2, x + sw - 1, 20,
                               fill=config.GREEN if status == "watching" else config.RED,
                               outline="")
            x += sw

    # ── Misc ──────────────────────────────────────────────────────────

    def _update_status(self, s):
        self.current_status = s
        t = {
            "idle":     ("● IDLE",     config.MUTED),
            "watching": ("● WATCHING", config.GREEN),
            "away":     ("● AWAY",     config.RED),
        }.get(s, ("● IDLE", config.MUTED))
        self.status_badge.config(text=t[0], fg=t[1])

    def _mode_changed(self):
        if self.mode.get() == "log":
            self.resume_outer.pack(fill="x", padx=16, pady=(0, 8))
        else:
            self.resume_outer.pack_forget()

    def _log(self, tag, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def export_log(self):
        if not self.logger.away_events:
            messagebox.showinfo("No Data", "No events yet."); return
        messagebox.showinfo("Exported", f"Saved to:\n{self.logger.export_csv()}")

    def on_close(self):
        if self.is_running: self._stop_guard()
        self.root.destroy()

    def run(self):
        self.root.after(200, self._draw_tl)
        self.root.mainloop()


# ── UI helper functions ───────────────────────────────────────────────

def _sep(p):
    """Horizontal separator line."""
    tk.Frame(p, bg=config.BG_DARK, height=1).pack(fill="x", padx=12, pady=4)


def _section_label(p, text):
    """Sidebar section header with left accent dot."""
    f = tk.Frame(p, bg=config.BG_MID)
    f.pack(fill="x", padx=12, pady=(8, 4))
    tk.Label(f, text="◈", font=("Courier New", 8),
             bg=config.BG_MID, fg=config.CYAN).pack(side="left")
    tk.Label(f, text=f"  {text}", font=("Courier New", 7, "bold"),
             bg=config.BG_MID, fg=config.MUTED).pack(side="left")


def _section_label_inline(p, text):
    """Inline section header for panels (used inside tab content frames)."""
    tk.Label(p, text=text, font=("Courier New", 8, "bold"),
             bg=config.BG_MID, fg=config.MUTED).pack(anchor="w", padx=10, pady=(8, 3))


if __name__ == "__main__":
    app = WatchGuardApp()
    app.run()
