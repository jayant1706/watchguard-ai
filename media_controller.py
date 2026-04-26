"""
WatchGuard AI - Media Controller v3
Priority:
  1. Browser Bridge (WebSocket) — full position + seek for ANY browser video
  2. playerctl (Linux MPRIS)    — desktop players
  3. Spacebar                   — universal last resort
"""

import time
import platform
import subprocess

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE    = 0.05
    PYAUTOGUI_OK = True
except ImportError:
    PYAUTOGUI_OK = False

PLATFORM = platform.system()


class MediaController:

    def __init__(self, bridge=None):
        """
        bridge: BrowserBridge instance (passed in from main).
                If None, browser control is disabled.
        """
        self.bridge      = bridge
        self.last_action = None
        self.last_action_time = 0.0
        self.methods     = []
        self._setup()

    def _setup(self):
        # Bridge added externally after init via set_bridge()
        if PLATFORM == "Linux":
            try:
                r = subprocess.run(["which", "playerctl"], capture_output=True)
                if r.returncode == 0:
                    self.methods.append("playerctl")
            except Exception:
                pass
        if PYAUTOGUI_OK:
            self.methods.append("spacebar")

    def set_bridge(self, bridge):
        self.bridge = bridge

    # ── Position ──────────────────────────────────────────────────────

    def get_position(self):
        """Returns current video position in seconds, or None."""
        if self.bridge and self.bridge.is_connected():
            pos, _ = self.bridge.get_position()
            if pos is not None:
                return pos

        if "playerctl" in self.methods:
            pos = self._playerctl_position()
            if pos is not None:
                return pos

        return None

    # ── Pause ─────────────────────────────────────────────────────────

    def pause(self):
        if self.last_action == "pause" and time.time() - self.last_action_time < 3.0:
            return True

        ok = False
        if self.bridge and self.bridge.is_connected():
            ok = self.bridge.pause()
            if ok:
                print("[MediaCtrl] PAUSE via browser bridge")
        if not ok and "playerctl" in self.methods:
            ok = self._playerctl("pause")
        if not ok:
            ok = self._spacebar("pause")

        if ok:
            self.last_action      = "pause"
            self.last_action_time = time.time()
        return ok

    # ── Play ──────────────────────────────────────────────────────────

    def play(self):
        if self.last_action == "play" and time.time() - self.last_action_time < 3.0:
            return True

        ok = False
        if self.bridge and self.bridge.is_connected():
            ok = self.bridge.play()
            if ok:
                print("[MediaCtrl] PLAY via browser bridge")
        if not ok and "playerctl" in self.methods:
            ok = self._playerctl("play")
        if not ok:
            ok = self._spacebar("play")

        if ok:
            self.last_action      = "play"
            self.last_action_time = time.time()
        return ok

    # ── Seek + play ───────────────────────────────────────────────────

    def play_from(self, position_seconds: float):
        """Seek to position and resume — the core of 'resume from timestamp'."""
        if self.bridge and self.bridge.is_connected():
            ok = self.bridge.seek_and_play(position_seconds)
            if ok:
                print(f"[MediaCtrl] SEEK+PLAY {position_seconds:.1f}s via browser bridge")
                self.last_action = "play"
                self.last_action_time = time.time()
                return True

        if "playerctl" in self.methods:
            self._playerctl_seek(position_seconds)
            time.sleep(0.3)
            return self._playerctl("play")

        # Spacebar fallback — can't seek, just resume
        print("[MediaCtrl] Seek unavailable — resuming in place")
        return self.play()
        print("[DEBUG] play_from called")

    def is_browser_connected(self):
        return bool(self.bridge and self.bridge.is_connected())

    # ── playerctl ─────────────────────────────────────────────────────

    def _playerctl(self, action):
        cmd = "pause" if action == "pause" else "play"
        try:
            r = subprocess.run(["playerctl", cmd], capture_output=True, timeout=2)
            return r.returncode == 0
        except Exception:
            return False

    def _playerctl_position(self):
        try:
            r = subprocess.run(["playerctl", "position"],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0 and r.stdout.strip():
                return float(r.stdout.strip())
        except Exception:
            pass
        return None

    def _playerctl_seek(self, seconds: float):
        try:
            r = subprocess.run(["playerctl", "position", str(seconds)],
                               capture_output=True, timeout=2)
            return r.returncode == 0
        except Exception:
            return False

    # ── Spacebar ──────────────────────────────────────────────────────

    def _spacebar(self, action):
        if not PYAUTOGUI_OK:
            return False
        needs = (
            self.last_action is None or
            (action == "pause" and self.last_action == "play") or
            (action == "play"  and self.last_action == "pause")
        )
        if needs:
            time.sleep(0.1)
            pyautogui.press("space")
            print(f"[MediaCtrl] Spacebar → {action}")
        return True
