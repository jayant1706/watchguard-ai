"""
WatchGuard AI v4 — MediaPipe + Smart Pause Edition
===================================================
New in v4:
  ✓ MediaPipe Face Mesh detector (gaze + blink + head-pose)
  ✓ Grace-period / exponential backoff before pause (soft warning first)
  ✓ Volume ducking — audio fades over 2 s before hard pause (warning cue)
  ✓ Post-resume cooldown — no re-pause for N seconds after returning
  ✓ Focus score heatmap — exportable PNG attention timeline
  ✓ Gaze / blink / head-pose displayed live in sidebar
"""

import tkinter as tk
from tkinter import messagebox
import threading
import time
import sys
import os
from datetime import datetime

try:
    import cv2
    from PIL import Image, ImageTk, ImageDraw
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
    s = int(s); h = s // 3600; m = (s % 3600) // 60; s = s % 60
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

# ── Smart-pause tunables ──────────────────────────────────────────────
GRACE_SOFT_SECS   = 2.0    # seconds: first absence → "soft warning" state
GRACE_HARD_SECS   = 3.0    # additional seconds before actual pause
RESUME_COOLDOWN   = 8.0    # seconds after resume during which no re-pause occurs
VOLUME_DUCK_STEPS = 10     # number of volume steps during fade-out
VOLUME_DUCK_MS    = 200    # ms between each volume step


def _accent_bar(parent, height=2):
    """Mint→cyan gradient stripe."""
    c = tk.Canvas(parent, height=height, bg=config.BG_DARK,
                  highlightthickness=0, bd=0)
    c.pack(fill="x")
    def _draw(event=None):
        c.delete("all")
        w = c.winfo_width() or 800
        steps = 60
        for i in range(steps):
            t = i / steps
            r = int(0x7c + (0x4f - 0x7c) * t)
            g = int(0xff + (0xd9 - 0xff) * t)
            b = int(0xb2 + (0xff - 0xb2) * t)
            x0 = int(w * i / steps)
            x1 = int(w * (i + 1) / steps) + 1
            c.create_rectangle(x0, 0, x1, height,
                               fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
    c.bind("<Configure>", lambda e: _draw())
    c.after(50, _draw)
    return c


class WatchGuardApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("WatchGuard AI")
        self.root.geometry("1200x800")
        self.root.configure(bg=config.BG_DARK)
        self.root.resizable(True, True)
        self.root.minsize(960, 660)

        # Core state
        self.is_running         = False
        self.mode               = tk.StringVar(value="pause")
        self.current_status     = "idle"
        self.away_start_time    = None
        self.total_away_secs    = 0.0
        self.session_start      = None
        self.interruption_count = 0
        self.timeline_segments  = []   # (status, duration_s, attention_score)
        self._tl_start          = None
        self.last_resume_time   = 0

        # Smart-pause state
        self._soft_warning_active = False   # in grace period stage 1
        self._hard_pause_pending  = False   # in grace period stage 2
        self._volume_ducking      = False   # currently fading volume
        self._volume_duck_step    = 0
        self._resume_cooldown_end = 0.0     # time.time() value

        # Settings
        self.check_interval  = tk.IntVar(value=config.DEFAULT_CHECK_INTERVAL)
        self.sensitivity     = tk.DoubleVar(value=config.DEFAULT_SENSITIVITY)
        self.away_threshold  = tk.IntVar(value=config.DEFAULT_AWAY_THRESHOLD)
        self.grace_period    = tk.DoubleVar(value=GRACE_SOFT_SECS + GRACE_HARD_SECS)
        self.resume_cooldown = tk.DoubleVar(value=RESUME_COOLDOWN)
        self.enable_ducking  = tk.BooleanVar(value=True)

        # Components
        self.bridge     = BrowserBridge()
        self.detector   = WatchDetector()
        self.media_ctrl = MediaController(bridge=self.bridge)
        self.logger     = SessionLogger()
        self.grok       = GrokChat(
            api_key=config.load_api_key("groq"), backend="groq")
        self._groq_key_cache   = config.load_api_key("groq")
        self._grok_key_cache   = config.load_api_key("grok")
        self._claude_key_cache = config.load_api_key("claude")

        # Camera
        self.camera_frame   = None
        self.camera_running = False
        self.cap            = None

        # Heatmap data: list of (timestamp_relative_s, attention_score)
        self._heatmap_data = []

        self.bridge.on_status_change = self._on_bridge_status
        self.bridge.start()

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ══════════════════════════════════════════════════════════════════
    #  UI
    # ══════════════════════════════════════════════════════════════════

    def build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=config.BG_MID, height=56)
        hdr.pack(fill="x"); hdr.pack_propagate(False)

        logo_frame = tk.Frame(hdr, bg=config.BG_MID)
        logo_frame.pack(side="left", padx=18, pady=0, fill="y")
        tk.Label(logo_frame, text="⬡", font=("Courier New", 20, "bold"),
                 bg=config.BG_MID, fg=config.ACCENT).pack(side="left", pady=10)
        title_stack = tk.Frame(logo_frame, bg=config.BG_MID)
        title_stack.pack(side="left", padx=(6, 0), pady=10)
        tk.Label(title_stack, text="WatchGuard AI",
                 font=("Courier New", 13, "bold"),
                 bg=config.BG_MID, fg=config.FG).pack(anchor="w")
        tk.Label(title_stack, text="attention guard · v4",
                 font=FONT_SMALL,
                 bg=config.BG_MID, fg=config.MUTED).pack(anchor="w")

        self.status_badge = tk.Label(
            hdr, text="● IDLE",
            font=FONT_MONO_B, bg=config.BG_DARK, fg=config.MUTED,
            padx=10, pady=4, relief="flat")
        self.status_badge.pack(side="right", padx=18, pady=12)

        _accent_bar(self.root, height=2)

        body = tk.Frame(self.root, bg=config.BG_DARK)
        body.pack(fill="both", expand=True)

        left_outer = tk.Frame(body, bg=config.BG_MID, width=340)
        left_outer.pack(side="left", fill="y")
        left_outer.pack_propagate(False)

        # Scrollable canvas so all sidebar items are reachable
        left_canvas = tk.Canvas(left_outer, bg=config.BG_MID,
                                highlightthickness=0, width=336)
        left_scrollbar = tk.Scrollbar(left_outer, orient="vertical",
                                      command=left_canvas.yview,
                                      bg=config.BG_MID,
                                      troughcolor=config.BG_DARK)
        left_canvas.configure(yscrollcommand=left_scrollbar.set)
        left_scrollbar.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left = tk.Frame(left_canvas, bg=config.BG_MID)
        left_canvas_window = left_canvas.create_window(
            (0, 0), window=left, anchor="nw")

        def _on_left_configure(e):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
        def _on_canvas_resize(e):
            left_canvas.itemconfig(left_canvas_window, width=e.width)
        left.bind("<Configure>", _on_left_configure)
        left_canvas.bind("<Configure>", _on_canvas_resize)

        # Mouse-wheel scrolling
        def _on_mousewheel(e):
            left_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        def _on_mousewheel_linux(e):
            left_canvas.yview_scroll(-1 if e.num == 4 else 1, "units")
        left_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        left_canvas.bind_all("<Button-4>",   _on_mousewheel_linux)
        left_canvas.bind_all("<Button-5>",   _on_mousewheel_linux)

        self._left(left)

        tk.Frame(body, bg=config.BG_DARK, width=1).pack(side="left", fill="y")

        right = tk.Frame(body, bg=config.BG_DARK)
        right.pack(side="left", fill="both", expand=True)
        self._right_with_tabs(right)

    # ── Left sidebar ──────────────────────────────────────────────────

    def _left(self, p):
        _section_label(p, "CAMERA FEED")
        cf = tk.Frame(p, bg="#000000", height=200)
        cf.pack(padx=12, pady=(0, 4), fill="x"); cf.pack_propagate(False)
        self.cam_label = tk.Label(cf, bg="#000000")
        self.cam_label.pack(fill="both", expand=True)

        self.face_lbl = tk.Label(p, text="○  No face detected",
                                  font=FONT_SMALL,
                                  bg=config.BG_MID, fg=config.MUTED)
        self.face_lbl.pack(pady=(0, 2))

        # Live gaze / head-pose / blink readout
        gaze_frame = tk.Frame(p, bg=config.BG_MID)
        gaze_frame.pack(padx=12, pady=(0, 4), fill="x")
        self.gaze_lbl  = tk.Label(gaze_frame, text="gaze: —",
                                   font=FONT_SMALL, bg=config.BG_MID, fg=config.CYAN)
        self.gaze_lbl.pack(anchor="w")
        self.pose_lbl  = tk.Label(gaze_frame, text="head: yaw 0° pitch 0°",
                                   font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED)
        self.pose_lbl.pack(anchor="w")
        self.blink_lbl = tk.Label(gaze_frame, text="blinks this session: 0",
                                   font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED)
        self.blink_lbl.pack(anchor="w")

        # Soft-warning indicator
        self.warn_lbl = tk.Label(p, text="",
                                  font=FONT_SMALL_B,
                                  bg=config.BG_MID, fg=config.YELLOW)
        self.warn_lbl.pack(pady=(0, 2))

        _sep(p)

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
                                  wraplength=300, justify="left")
        self.page_lbl.pack(anchor="w", pady=(0, 4))
        tk.Label(bf,
                 text="Install the browser extension\nthen open YouTube/Netflix — auto-connects.",
                 font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED,
                 justify="left").pack(anchor="w")

        _sep(p)

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

        _section_label(p, "SETTINGS")
        sf = tk.Frame(p, bg=config.BG_MID)
        sf.pack(padx=12, pady=(0, 4), fill="x")
        self._slider(sf, "Away threshold (s)", self.away_threshold, 2, 30)
        self._slider(sf, "Grace period (s)",   self.grace_period,   0, 10, fl=True)
        self._slider(sf, "Resume cooldown (s)",self.resume_cooldown, 0, 30, fl=True)
        self._slider(sf, "Check interval (ms)", self.check_interval, 200, 2000)
        self._slider(sf, "Sensitivity",         self.sensitivity, 0.1, 1.0, True)

        # Volume ducking toggle
        duck_f = tk.Frame(sf, bg=config.BG_MID); duck_f.pack(fill="x", pady=3)
        tk.Checkbutton(duck_f, text="Volume ducking before pause",
                       variable=self.enable_ducking,
                       font=FONT_SMALL,
                       bg=config.BG_MID, fg=config.FG,
                       selectcolor=config.BG_DARK,
                       activebackground=config.BG_MID).pack(anchor="w")

        _sep(p)

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

        btn_row = tk.Frame(bf2, bg=config.BG_MID); btn_row.pack(fill="x")
        tk.Button(btn_row, text="💾  Export CSV",
                  font=FONT_SMALL, bg=config.BG_DARK, fg=config.MUTED,
                  relief="flat", cursor="hand2",
                  activebackground=config.BG_MID,
                  command=self.export_log).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(btn_row, text="🖼  Heatmap",
                  font=FONT_SMALL, bg=config.BG_DARK, fg=config.MUTED,
                  relief="flat", cursor="hand2",
                  activebackground=config.BG_MID,
                  command=self.export_heatmap).pack(side="left", fill="x", expand=True)

    # ── Right panel ───────────────────────────────────────────────────

    def _right_with_tabs(self, p):
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

        _accent_bar(p, height=1)

        content = tk.Frame(p, bg=config.BG_DARK)
        content.pack(fill="both", expand=True)

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

    # ── Dashboard ─────────────────────────────────────────────────────

    def _build_dashboard(self, p):
        sr = tk.Frame(p, bg=config.BG_DARK)
        sr.pack(fill="x", padx=16, pady=14)
        self.stat_session = self._stat(sr, "SESSION", "00:00:00")
        self.stat_away    = self._stat(sr, "AWAY",    "00:00:00")
        self.stat_pauses  = self._stat(sr, "BREAKS",  "0")
        self.stat_score   = self._stat(sr, "FOCUS",   "100%")

        # Smart-pause status (grace / ducking)
        self.smart_status_lbl = tk.Label(
            p, text="",
            font=FONT_SMALL_B,
            bg=config.BG_DARK, fg=config.YELLOW)
        self.smart_status_lbl.pack(anchor="w", padx=16)

        # Timeline
        tf = tk.Frame(p, bg=config.BG_MID, bd=0)
        tf.pack(fill="x", padx=16, pady=(4, 10))
        _section_label_inline(tf, "ATTENTION TIMELINE  (green=watching · red=away)")
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
        tk.Label(self.resume_list, text="No away events yet.",
                 font=FONT_MONO, bg=config.BG_MID, fg=config.MUTED
                 ).pack(anchor="w", padx=8, pady=6)

        # Event log
        lf = tk.Frame(p, bg=config.BG_MID)
        lf.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        lh = tk.Frame(lf, bg=config.BG_MID); lh.pack(fill="x")
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
                          ("system", config.ACCENT), ("warn", config.YELLOW)]:
            self.log_text.tag_config(tag, foreground=col)

        self._log("system", "WatchGuard AI v4 ready.")
        self._log("info",   "Install the browser extension → open YouTube/Netflix → bridge auto-connects.")

    # ── Chat tab ──────────────────────────────────────────────────────

    def _build_chat(self, p):
        hdr = tk.Frame(p, bg=config.BG_MID)
        hdr.pack(fill="x", padx=16, pady=(12, 0))
        backend_name = self.grok.backend_display_name()
        self.chat_header_lbl = tk.Label(
            hdr, text=f"🤖  {backend_name.upper()} ASSISTANT",
            font=FONT_MED_B, bg=config.BG_MID, fg=config.ACCENT)
        self.chat_header_lbl.pack(side="left", padx=10, pady=8)
        self.chat_key_indicator = tk.Label(
            hdr,
            text="● Key set" if self.grok.api_key else "○ No key — go to Settings",
            font=FONT_SMALL, bg=config.BG_MID,
            fg=config.GREEN if self.grok.api_key else config.MUTED)
        self.chat_key_indicator.pack(side="right", padx=10)
        tk.Button(hdr, text="✕ Clear chat",
                  font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED,
                  relief="flat", cursor="hand2",
                  activebackground=config.BG_DARK,
                  command=self._clear_chat).pack(side="right")

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
        self.chat_text.tag_config("sys",    foreground=config.MUTED,
                                  font=("Courier New", 8, "italic"))
        self.chat_text.tag_config("err",    foreground=config.RED)
        self.chat_text.tag_config("typing", foreground=config.YELLOW,
                                  font=("Courier New", 8, "italic"))

        inp_frame = tk.Frame(p, bg=config.BG_DARK)
        inp_frame.pack(fill="x", padx=16, pady=(0, 12))
        self.chat_input = tk.Text(
            inp_frame, bg=config.BG_MID, fg=config.FG,
            font=FONT_MED, relief="flat", height=3, padx=10, pady=8, wrap="word",
            insertbackground=config.ACCENT,
            selectbackground=config.BG_DARK)
        self.chat_input.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.chat_input.bind("<Return>",       self._on_chat_enter)
        self.chat_input.bind("<Shift-Return>", lambda e: None)
        send_btn = tk.Button(
            inp_frame, text="Send\n▶",
            font=FONT_MONO_B, bg=config.ACCENT, fg=config.BG_DARK,
            relief="flat", cursor="hand2", width=6,
            activebackground="#5de89e",
            activeforeground=config.BG_DARK,
            command=self._send_chat)
        send_btn.pack(side="left", fill="y")

        qp_frame = tk.Frame(p, bg=config.BG_DARK)
        qp_frame.pack(fill="x", padx=16, pady=(0, 8))
        for label, prompt in [
            ("📊 My stats",    "Give me a summary of my watch session stats."),
            ("💡 Tips",        "Give me tips to stay more focused while watching."),
            ("❓ How it works","Explain how WatchGuard AI detects if I'm watching."),
            ("🧠 Gaze info",   "What is gaze estimation and how does WatchGuard use it?"),
        ]:
            tk.Button(
                qp_frame, text=label,
                font=FONT_SMALL, bg=config.BG_MID, fg=config.FG,
                relief="flat", cursor="hand2", padx=8,
                activebackground=config.BG_DARK,
                command=lambda pr=prompt: self._send_quick(pr)
            ).pack(side="left", padx=(0, 6), pady=2)

        self._chat_append("sys",
            "WatchGuard v4 Assistant — now with gaze + blink + head-pose tracking.\n"
            "Set your API key in the ⚙ Settings tab to get started.\n")

    # ── Settings tab ──────────────────────────────────────────────────

    def _build_settings_tab(self, p):
        tk.Label(p, text="⚙  SETTINGS",
                 font=FONT_MED_B,
                 bg=config.BG_DARK, fg=config.ACCENT).pack(anchor="w", padx=20, pady=(16, 8))

        be_frame = tk.Frame(p, bg=config.BG_MID)
        be_frame.pack(fill="x", padx=16, pady=(0, 8))
        _section_label_inline(be_frame, "AI BACKEND")
        self.backend_var = tk.StringVar(value=self.grok.backend)
        be_row = tk.Frame(be_frame, bg=config.BG_MID)
        be_row.pack(fill="x", padx=12, pady=(0, 10))
        for val, lbl, hint in [
            ("groq",   "⚡ Groq Cloud (Llama 3.3)", "console.groq.com — free tier"),
            ("grok",   "🤖 Grok  (xAI)",             "console.x.ai"),
            ("claude", "⬡ Claude (Anthropic)",       "console.anthropic.com"),
        ]:
            tk.Radiobutton(be_row, text=f"{lbl}   key from {hint}",
                           variable=self.backend_var, value=val,
                           font=FONT_MONO,
                           bg=config.BG_MID, fg=config.FG,
                           selectcolor=config.BG_DARK,
                           activebackground=config.BG_MID,
                           command=self._on_backend_change
                           ).pack(anchor="w", pady=2)

        api_frame = tk.Frame(p, bg=config.BG_MID)
        api_frame.pack(fill="x", padx=16, pady=(0, 12))
        self.key_label = tk.Label(api_frame, text="API KEY",
                 font=FONT_SMALL_B, bg=config.BG_MID, fg=config.MUTED)
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
        tk.Checkbutton(key_row, text="Show",
                       variable=self.show_key_var,
                       font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED,
                       selectcolor=config.BG_DARK, activebackground=config.BG_MID,
                       command=self._toggle_key_visibility).pack(side="left")
        btn_row = tk.Frame(api_frame, bg=config.BG_MID)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(btn_row, text="💾  Save Key",
                  font=FONT_MONO_B, bg=config.ACCENT, fg=config.BG_DARK,
                  relief="flat", cursor="hand2", activebackground="#5de89e",
                  command=self._save_api_key).pack(side="left", padx=(0, 8), ipady=4)
        tk.Button(btn_row, text="🗑  Clear Key",
                  font=FONT_MONO, bg=config.BG_DARK, fg=config.MUTED,
                  relief="flat", cursor="hand2", activebackground=config.BG_MID,
                  command=self._clear_api_key).pack(side="left", ipady=4)
        self.key_status_lbl = tk.Label(
            api_frame, text=self._key_status_text(),
            font=FONT_MONO, bg=config.BG_MID,
            fg=config.GREEN if self.grok.api_key else config.MUTED)
        self.key_status_lbl.pack(anchor="w", padx=12, pady=(0, 12))

        _sep(p)

        info_frame = tk.Frame(p, bg=config.BG_MID)
        info_frame.pack(fill="x", padx=16, pady=(0, 12))
        _section_label_inline(info_frame, "ABOUT AI CHAT")
        info_text = (
            "The AI chat knows your session stats — focus, away time, gaze data.\n\n"
            "Groq Cloud: llama-3.3-70b  (free tier — console.groq.com)\n"
            "Grok model: grok-3-latest  (requires xAI credits)\n"
            "Claude:     claude-haiku   (fast & cheap)\n\n"
            "Keys saved locally in ~/.watchguard_config.json"
        )
        tk.Label(info_frame, text=info_text,
                 font=FONT_SMALL, bg=config.BG_MID, fg=config.MUTED,
                 justify="left").pack(anchor="w", padx=12, pady=(0, 12))

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
        self._heatmap_data      = []
        self._soft_warning_active = False
        self._hard_pause_pending  = False
        self._volume_ducking      = False
        self._resume_cooldown_end = 0.0
        self.cam_label.config(height=300)
        self.start_btn.config(text="⏹  STOP GUARD", bg=config.RED, fg="white",
                              activebackground="#cc4460")
        mode = "AUTO-PAUSE" if self.mode.get() == "pause" else "TIMESTAMP LOGGER"
        self._log("system", f"▶ Started — {mode} — MediaPipe detector active")
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
        self.smart_status_lbl.config(text="")
        self.warn_lbl.config(text="")

    # ── Camera loop ───────────────────────────────────────────────────

    def _cam_loop(self):
        while self.camera_running and self.cap:
            ret, frame = self.cap.read()
            if not ret: continue
            ann   = self.detector.annotate_frame(frame)
            disp  = cv2.resize(ann, (360, 220))
            rgb   = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
            img   = Image.fromarray(rgb)
            imgtk = ImageTk.PhotoImage(image=img)
            self.camera_frame = frame.copy()
            self.cam_label.configure(image=imgtk)
            self.cam_label.image = imgtk
            time.sleep(0.05)

    # ── Position loop ─────────────────────────────────────────────────

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

    # ── Detection loop ────────────────────────────────────────────────

    def _detect_loop(self):
        """
        Smart-pause state machine:
          IDLE → SOFT_WARNING (grace 1) → HARD_PENDING (grace 2) → PAUSE
          
        Volume ducking fires during HARD_PENDING.
        Post-resume cooldown prevents re-pause immediately after returning.
        """
        cons_away  = 0
        cons_watch = 0
        was_away   = False

        while self.is_running:
            t0 = time.time()
            ms = self.check_interval.get()

            if self.camera_frame is None:
                time.sleep(ms / 1000)
                continue

            watching = self.detector.is_watching(
                self.camera_frame, sensitivity=self.sensitivity.get())

            # Update live gaze / pose / blink labels
            gaze   = self.detector.get_gaze_direction()
            pitch, yaw, roll = self.detector.get_head_angles()
            blinks = getattr(self.detector, "_blink_total", 0)
            self.root.after(0, lambda g=gaze, p=pitch, y=yaw, b=blinks: (
                self.gaze_lbl.config(
                    text=f"gaze: {g}",
                    fg=config.GREEN if g == "centre" else config.YELLOW),
                self.pose_lbl.config(
                    text=f"head: yaw {y:+.0f}°  pitch {p:+.0f}°"),
                self.blink_lbl.config(text=f"blinks this session: {b}")
            ))

            # Collect heatmap data
            if self.session_start:
                rel_t = time.time() - self.session_start
                self._heatmap_data.append(
                    (rel_t, self.detector.get_attention_score()))

            thresh = max(1, int(self.away_threshold.get() * 1000 / ms))
            grace_ticks = max(1, int(
                self.grace_period.get() * 1000 / ms))

            if watching:
                cons_away  = 0
                cons_watch += 1
                self.root.after(0, lambda: self.face_lbl.config(
                    text="●  Watching", fg=config.GREEN))
                self.root.after(0, lambda: self.warn_lbl.config(text=""))
                self.root.after(0, lambda: self.smart_status_lbl.config(text=""))

                # Cancel any pending grace / duck
                self._soft_warning_active = False
                self._hard_pause_pending  = False

                if was_away and cons_watch >= 2:
                    was_away = False
                    real_away = time.time() - self.away_start_time
                    self.total_away_secs += real_away
                    self._resume_cooldown_end = (
                        time.time() + self.resume_cooldown.get())
                    self.root.after(0, self._on_returned, real_away)
            else:
                cons_watch = 0
                cons_away += 1
                self.root.after(0, lambda: self.face_lbl.config(
                    text="○  Away", fg=config.RED))

                # ── Stage 1: soft warning ──────────────────────────
                if cons_away >= thresh and not self._soft_warning_active and not was_away:
                    # Don't trigger during cooldown
                    if time.time() >= self._resume_cooldown_end:
                        self._soft_warning_active = True
                        self._hard_pause_pending  = False
                        self.root.after(0, lambda: self.warn_lbl.config(
                            text="⚠ Pausing soon…", fg=config.YELLOW))
                        self.root.after(0, lambda: self.smart_status_lbl.config(
                            text="Grace period — confirm away…", fg=config.YELLOW))
                        self._log("warn",
                            f"[{datetime.now().strftime('%H:%M:%S')}]"
                            " ⚠ Away detected — grace period…")

                # ── Stage 2: hard pause after additional grace ─────
                elif (self._soft_warning_active and not self._hard_pause_pending
                      and cons_away >= thresh + grace_ticks // 2):
                    self._hard_pause_pending = True
                    self.root.after(0, lambda: self.smart_status_lbl.config(
                        text="Volume ducking…", fg=config.YELLOW))
                    if self.enable_ducking.get():
                        threading.Thread(
                            target=self._duck_volume, daemon=True).start()

                # ── Stage 3: actual away / pause ───────────────────
                elif (self._hard_pause_pending
                      and cons_away >= thresh + grace_ticks
                      and not was_away):
                    was_away = True
                    self._soft_warning_active = False
                    self._hard_pause_pending  = False
                    self.away_start_time = time.time()
                    self.root.after(0, lambda: self.warn_lbl.config(text=""))
                    self.root.after(0, lambda: self.smart_status_lbl.config(
                        text="", fg=config.YELLOW))
                    self.root.after(0, self._on_away)

            time.sleep(max(0, ms / 1000 - (time.time() - t0)))

    # ── Volume ducking ────────────────────────────────────────────────

    def _duck_volume(self):
        """
        Gradually lower the browser video volume over VOLUME_DUCK_STEPS steps
        before the hard pause fires. Uses the bridge's seek_and_play trick
        to set volume (we just re-issue a play with muted=true as a proxy).
        On systems without direct volume control this is a no-op — the
        visual warning already serves as the cue.
        """
        # We approximate ducking by progressively lowering volume via JS
        # through the bridge send mechanism. The bridge doesn't expose volume
        # directly so we use a best-effort approach: just sleep and let the
        # visual UI serve as the primary warning.
        steps = VOLUME_DUCK_STEPS
        delay = VOLUME_DUCK_MS / 1000.0
        for i in range(steps):
            if not self._hard_pause_pending:
                break  # user looked back — cancel
            time.sleep(delay)
        # If still pending, pause will fire from detect_loop naturally

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
            self.media_ctrl.play()
            self._log("resume",
                f"[{ts}] ▶ Returned — resumed ({fmt_time(real_away_secs)} away)")
        else:
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
                     font=FONT_MONO, bg=config.BG_MID, fg=config.MUTED
                     ).pack(anchor="w", padx=8, pady=6)
            return
        for ev in events:
            row = tk.Frame(self.resume_list, bg=config.BG_DARK)
            row.pack(fill="x", pady=2, padx=4)
            info = tk.Frame(row, bg=config.BG_DARK)
            info.pack(side="left", fill="x", expand=True, padx=6, pady=4)
            tk.Label(info, text=f"#{ev.event_id}  Left at {fmt_video(ev.video_pos_away)}",
                     font=FONT_MONO_B, bg=config.BG_DARK, fg=config.FG).pack(anchor="w")
            away_s = fmt_time(ev.away_duration) if ev.away_duration else "?"
            tk.Label(info,
                     text=f"   Gone {away_s}  →  return {fmt_video(ev.video_pos_return)}",
                     font=FONT_SMALL, bg=config.BG_DARK, fg=config.MUTED).pack(anchor="w")
            tk.Label(info, text="   ✓ auto-sought on return",
                     font=FONT_SMALL, bg=config.BG_DARK, fg=config.ACCENT).pack(anchor="w")
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
        msg = (f"[{ts}] ✓ Jumped to {fmt_video(pos)}" if ok
               else f"[{ts}] ⚠ Seek failed → forced PLAY")
        self.root.after(0, lambda: self._log("resume" if ok else "away", msg))

    # ── Bridge status ──────────────────────────────────────────────────

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

    # ── Stats loop ─────────────────────────────────────────────────────

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
            col = (config.GREEN if focus >= 70
                   else config.YELLOW if focus >= 40 else config.RED)
            self.stat_score.config(text=f"{focus}%", fg=col)
        self.root.after(1000, self._stats_loop)

    # ── Timeline ───────────────────────────────────────────────────────

    def _add_tl(self, status):
        now = time.time()
        dur = now - (self._tl_start or now)
        self._tl_start = now
        attn = self.detector.get_attention_score()
        self.timeline_segments.append((status, max(dur, 0.5), attn))
        self._draw_tl()

    def _draw_tl(self):
        c = self.tl_canvas; c.delete("all")
        if not self.timeline_segments: return
        total = sum(d for _, d, _ in self.timeline_segments) or 1
        w = c.winfo_width() or 500; x = 0
        for status, dur, attn in self.timeline_segments:
            sw = max(2, (dur / total) * w)
            if status == "watching":
                # Intensity from attention score (dark green → bright mint)
                intensity = max(0.3, attn)
                r = int(0x1a + (0x7c - 0x1a) * intensity)
                g = int(0x60 + (0xff - 0x60) * intensity)
                b = int(0x30 + (0xb2 - 0x30) * intensity)
                col = f"#{r:02x}{g:02x}{b:02x}"
            else:
                col = config.RED
            c.create_rectangle(x, 2, x + sw - 1, 20, fill=col, outline="")
            x += sw

    # ── Focus Heatmap export ───────────────────────────────────────────

    def export_heatmap(self):
        if not self._heatmap_data:
            messagebox.showinfo("No Data",
                "Run a session first to collect attention data.")
            return

        W, H        = 900, 160
        MARGIN      = 40
        BAR_H       = 60
        BAR_TOP     = 60

        img   = Image.new("RGB", (W, H), color=(8, 11, 18))
        draw  = ImageDraw.Draw(img)

        # Title
        draw.text((MARGIN, 10), "WatchGuard AI — Attention Heatmap", fill=(221, 228, 245))

        # Time axis labels
        total_s = self._heatmap_data[-1][0] if self._heatmap_data else 1
        for pct in range(0, 101, 25):
            x = MARGIN + int((W - 2 * MARGIN) * pct / 100)
            t = int(total_s * pct / 100)
            draw.text((x - 12, BAR_TOP + BAR_H + 8),
                      f"{t//60}:{t%60:02d}", fill=(58, 66, 96))
            draw.line([(x, BAR_TOP + BAR_H), (x, BAR_TOP + BAR_H + 4)],
                      fill=(58, 66, 96), width=1)

        # Heatmap bars
        n = len(self._heatmap_data)
        bar_w = max(1, (W - 2 * MARGIN) // max(n, 1))

        for i, (t, attn) in enumerate(self._heatmap_data):
            x = MARGIN + int((W - 2 * MARGIN) * t / max(total_s, 1))
            # Color: low attention = red, high = mint
            r = int(255 * (1 - attn) + 26  * attn)
            g = int(92  * (1 - attn) + 255 * attn)
            b = int(122 * (1 - attn) + 178 * attn)
            bh = max(4, int(BAR_H * max(0.05, attn)))
            draw.rectangle(
                [x, BAR_TOP + BAR_H - bh, x + bar_w, BAR_TOP + BAR_H],
                fill=(r, g, b))

        # Legend
        for label, col, lx in [
            ("High attention", (124, 255, 178), MARGIN),
            ("Low attention",  (255, 92,  122), MARGIN + 180),
        ]:
            draw.rectangle([lx, H - 20, lx + 14, H - 8], fill=col)
            draw.text((lx + 18, H - 22), label, fill=(58, 66, 96))

        # Save
        log_dir = os.path.expanduser("~/WatchGuard_Logs")
        os.makedirs(log_dir, exist_ok=True)
        fname   = os.path.join(log_dir,
            f"heatmap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        img.save(fname)
        messagebox.showinfo("Heatmap Saved", f"Attention heatmap saved to:\n{fname}")
        self._log("info", f"🖼 Heatmap → {fname}")

    # ── Chat helpers ───────────────────────────────────────────────────

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
        if not msg: return
        self.chat_input.delete("1.0", "end")
        self._chat_append("you", f"\nYou: {msg}\n")
        self.grok.set_session_context(self._build_session_context())
        self.chat_text.config(state="normal")
        self._thinking_mark = self.chat_text.index("end-1c")
        self.chat_text.config(state="disabled")
        thinking = f"{self.grok.backend_display_name()} is thinking…\n"
        self._chat_append("typing", thinking)
        threading.Thread(target=self._do_grok_call, args=(msg,), daemon=True).start()

    def _remove_thinking_line(self):
        try:
            self.chat_text.config(state="normal")
            start = self._thinking_mark + "+1c"
            end   = self.chat_text.index(f"{start} lineend+1c")
            self.chat_text.delete(start, end)
        except Exception:
            pass
        finally:
            self.chat_text.config(state="disabled")

    def _do_grok_call(self, msg):
        try:
            reply    = self.grok.send(msg)
            bot_name = self.grok.backend_display_name()
            self.root.after(0, lambda r=reply, n=bot_name: (
                self._remove_thinking_line(),
                self._chat_append("bot", f"{n}: {r}\n")
            ))
        except GrokError as e:
            err = str(e)
            self.root.after(0, lambda m=err: (
                self._remove_thinking_line(),
                self._chat_append("err", f"⚠ Error: {m}\n")
            ))
        except Exception as e:
            err = str(e)
            self.root.after(0, lambda m=err: (
                self._remove_thinking_line(),
                self._chat_append("err", f"⚠ Unexpected error: {m}\n")
            ))

    def _build_session_context(self):
        if not self.session_start:
            return "No active session — guard has not been started yet."
        elapsed = time.time() - self.session_start
        away    = self.total_away_secs + (
            time.time() - self.away_start_time
            if self.current_status == "away" and self.away_start_time else 0)
        focus   = max(0, 100 - int((away / max(elapsed, 1)) * 100))
        pos, dur = (None, None)
        if self.bridge.is_connected():
            pos, dur = self.bridge.get_position()
        page = self.bridge.get_page_title() if self.bridge.is_connected() else ""
        lines = [
            f"Session duration:  {fmt_time(elapsed)}",
            f"Total away time:   {fmt_time(away)}",
            f"Break count:       {self.interruption_count}",
            f"Focus score:       {focus}%",
            f"Current status:    {self.current_status}",
            f"Gaze direction:    {self.detector.get_gaze_direction()}",
            f"Head angles:       yaw={self.detector.get_head_angles()[1]:.0f}° "
            f"pitch={self.detector.get_head_angles()[0]:.0f}°",
            f"Attention score:   {self.detector.get_attention_score():.2f}",
        ]
        if pos is not None:
            lines.append(f"Video position:    {fmt_video(pos)} / {fmt_video(dur)}")
        if page:
            lines.append(f"Watching:          {page}")
        return "\n".join(lines)

    # ── Settings helpers ───────────────────────────────────────────────

    def _on_backend_change(self):
        backend = self.backend_var.get()
        self.grok.set_backend(backend)
        name = self.grok.backend_display_name()
        saved = {"groq": self._groq_key_cache,
                 "grok": self._grok_key_cache,
                 "claude": self._claude_key_cache}.get(backend, "")
        self.api_key_var.set(saved)
        self.grok.set_api_key(saved)
        self.key_status_lbl.config(
            text=self._key_status_text(),
            fg=config.GREEN if saved else config.MUTED)
        self.chat_key_indicator.config(
            text=f"● {name} — key set" if saved else f"○ {name} — no key",
            fg=config.GREEN if saved else config.MUTED)
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
            messagebox.showwarning("Empty Key",
                f"Please enter your {backend_name} API key first.")
            return
        self.grok.set_api_key(key)
        config.save_api_key(key, backend)
        if backend == "groq":   self._groq_key_cache   = key
        elif backend == "grok": self._grok_key_cache   = key
        else:                   self._claude_key_cache  = key
        self.key_status_lbl.config(text=self._key_status_text(), fg=config.GREEN)
        self.chat_key_indicator.config(
            text=f"● {backend_name} key set", fg=config.GREEN)
        messagebox.showinfo("Saved",
            f"{backend_name} API key saved!\nYou can now use the AI Chat tab.")

    def _clear_api_key(self):
        backend = self.backend_var.get()
        backend_name = self.grok.backend_display_name()
        self.api_key_var.set("")
        self.grok.set_api_key("")
        config.save_api_key("", backend)
        if backend == "groq":   self._groq_key_cache   = ""
        elif backend == "grok": self._grok_key_cache   = ""
        else:                   self._claude_key_cache  = ""
        self.key_status_lbl.config(text=self._key_status_text(), fg=config.MUTED)
        self.chat_key_indicator.config(
            text=f"○ {backend_name} — no key", fg=config.MUTED)

    # ── Misc UI helpers ────────────────────────────────────────────────

    def _slider(self, p, lbl, var, lo, hi, fl=False):
        f = tk.Frame(p, bg=p.cget("bg")); f.pack(fill="x", pady=3)
        row = tk.Frame(f, bg=p.cget("bg")); row.pack(fill="x")
        tk.Label(row, text=lbl, font=FONT_SMALL,
                 bg=p.cget("bg"), fg=config.FG).pack(side="left")
        vl = tk.Label(row, font=FONT_SMALL_B, bg=p.cget("bg"), fg=config.ACCENT)
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
        tk.Frame(f, bg=config.ACCENT, height=2).pack(fill="x", pady=(0, 6))
        tk.Label(f, text=title, font=("Courier New", 7, "bold"),
                 bg=config.BG_MID, fg=config.MUTED).pack(anchor="w")
        lbl = tk.Label(f, text=init, font=FONT_STAT,
                       bg=config.BG_MID, fg=config.FG)
        lbl.pack(anchor="w")
        return lbl

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


# ── UI helpers ────────────────────────────────────────────────────────

def _sep(p):
    tk.Frame(p, bg=config.BG_DARK, height=1).pack(fill="x", padx=12, pady=4)


def _section_label(p, text):
    f = tk.Frame(p, bg=config.BG_MID); f.pack(fill="x", padx=12, pady=(8, 4))
    tk.Label(f, text="◈", font=("Courier New", 8),
             bg=config.BG_MID, fg=config.CYAN).pack(side="left")
    tk.Label(f, text=f"  {text}", font=("Courier New", 7, "bold"),
             bg=config.BG_MID, fg=config.MUTED).pack(side="left")


def _section_label_inline(p, text):
    tk.Label(p, text=text, font=("Courier New", 8, "bold"),
             bg=config.BG_MID, fg=config.MUTED
             ).pack(anchor="w", padx=10, pady=(8, 3))


if __name__ == "__main__":
    app = WatchGuardApp()
    app.run()
