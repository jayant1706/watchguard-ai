"""
WatchGuard AI - Browser Bridge Server
Runs a WebSocket server on ws://localhost:8765.
The browser extension connects here, giving Python full control over
video.currentTime in any browser tab (YouTube, Netflix, Prime, etc.)
"""

import asyncio
import json
import threading
import time
import websockets
import websockets.server


class BrowserBridge:
    """
    Async WebSocket server. The browser extension connects as a client.
    Python code calls the synchronous helpers (get_position, pause, play, seek_and_play)
    from any thread — they post requests and wait for responses.
    """

    WS_PORT = 8765

    def __init__(self):
        self._client        = None      # the connected extension websocket
        self._lock          = threading.Lock()
        self._response_evt  = threading.Event()
        self._pending_resp  = None
        self._last_position = None      # most recent POSITION_UPDATE pushed by extension
        self._last_pos_time = 0.0
        self._loop          = None
        self._ready         = threading.Event()
        self._connected     = False
        self.on_status_change = None    # callback(connected: bool)

    # ── Start in background thread ────────────────────────────────────

    def start(self):
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()
        self._ready.wait(timeout=5)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self):
        async with websockets.server.serve(
            self._handler, "0.0.0.0", self.WS_PORT,
            ping_interval=10, ping_timeout=5
        ):
            print(f"[Bridge] WebSocket server on ws://localhost:{self.WS_PORT}")
            self._ready.set()   # signal only after server is successfully bound
            await asyncio.Future()   # run forever

    # ── WebSocket handler ─────────────────────────────────────────────

    async def _handler(self, ws):
        self._client    = ws
        self._connected = True
        print("[Bridge] Browser extension connected ✓")
        if self.on_status_change:
            self.on_status_change(True)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self._handle_message(msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._client    = None
            self._connected = False
            print("[Bridge] Browser extension disconnected")
            if self.on_status_change:
                self.on_status_change(False)

    async def _handle_message(self, msg):
        mtype = msg.get("type", "")

        if mtype == "POSITION_UPDATE":
            # Extension pushes position every 500 ms automatically
            self._last_position = msg
            self._last_pos_time = time.time()

        elif mtype in ("POSITION_RESPONSE", "PAUSE_RESPONSE",
                       "PLAY_RESPONSE", "SEEK_RESPONSE", "PONG"):
            # Response to a command we sent
            self._pending_resp = msg
            self._response_evt.set()

        elif mtype == "EXTENSION_READY":
            print(f"[Bridge] Extension ready, version {msg.get('version','?')}")

    # ── Async send helper ─────────────────────────────────────────────

    async def _send(self, payload: dict):
        if self._client is None:
            return None
        self._response_evt.clear()
        self._pending_resp = None
        await self._client.send(json.dumps(payload))

    def _send_sync(self, payload: dict, timeout: float = 2.0):
        """Thread-safe: post a command and wait for the response."""
        if self._loop is None or not self._connected:
            return None
        self._response_evt.clear()
        self._pending_resp = None
        asyncio.run_coroutine_threadsafe(
            self._send(payload), self._loop
        ).result(timeout=1.0)
        self._response_evt.wait(timeout=timeout)
        return self._pending_resp

    # ── Public synchronous API (called from Python main thread) ───────

    def is_connected(self):
        return self._connected

    def get_position(self):
        """
        Returns (currentTime_seconds, duration_seconds) or (None, None).
        Uses the push-based cache first (updated every 500 ms by extension),
        falls back to a GET_POSITION request.
        """
        # Use cached value if fresh (< 1.5 s old)
        if self._last_position and time.time() - self._last_pos_time < 1.5:
            ct = self._last_position.get("currentTime")
            dur = self._last_position.get("duration", 0)
            return (float(ct) if ct is not None else None, float(dur))

        # Ask explicitly
        resp = self._send_sync({"type": "GET_POSITION"})
        if resp:
            ct  = resp.get("currentTime")
            dur = resp.get("duration", 0)
            return (float(ct) if ct is not None else None, float(dur))
        return (None, None)

    def pause(self):
        """Pause the browser video. Returns True on success."""
        resp = self._send_sync({"type": "PAUSE"})
        return bool(resp and resp.get("ok"))

    def play(self):
        """Resume the browser video. Returns True on success."""
        resp = self._send_sync({"type": "PLAY"})
        return bool(resp and resp.get("ok"))

    def seek_and_play(self, position_seconds: float):
        """Seek to position_seconds and resume. Returns True on success."""
        resp = self._send_sync({
            "type": "SEEK_AND_PLAY",
            "position": float(position_seconds),
        })
        return bool(resp and resp.get("ok"))

    def seek(self, position_seconds: float):
        """Seek without changing play/pause state."""
        resp = self._send_sync({
            "type": "SEEK",
            "position": float(position_seconds),
        })
        return bool(resp and resp.get("ok"))

    def get_page_title(self):
        if self._last_position:
            return self._last_position.get("title", "")
        return ""

    def get_page_url(self):
        if self._last_position:
            return self._last_position.get("url", "")
        return ""
