"""
WatchGuard AI - AI Chat Module
Supports three backends:
  - Groq Cloud (groq)   -- uses official groq SDK (avoids Cloudflare blocks)
  - Grok  (xAI)         -- https://api.x.ai/v1/chat/completions
  - Claude (Anthropic)  -- https://api.anthropic.com/v1/messages
"""

import json
import urllib.request
import urllib.error

try:
    from groq import Groq as _GroqSDK
    GROQ_SDK_AVAILABLE = True
except ImportError:
    GROQ_SDK_AVAILABLE = False

BACKEND_GROQ   = "groq"
BACKEND_GROK   = "grok"
BACKEND_CLAUDE = "claude"

GROQ_MODEL     = "llama-3.3-70b-versatile"

GROK_URL       = "https://api.x.ai/v1/chat/completions"
GROK_MODEL     = "grok-3-latest"

CLAUDE_URL     = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL   = "claude-haiku-4-5-20251001"
CLAUDE_VERSION = "2023-06-01"

SYSTEM_PROMPT = (
    "You are WatchGuard Assistant, an AI helper embedded inside WatchGuard AI -- "
    "a smart screen-guard app that uses webcam face detection to auto-pause videos when the user "
    "looks away. You help users understand their watch session stats, answer questions about the app, "
    "and provide general assistance. Be concise, friendly, and helpful. When session data is provided, "
    "refer to it naturally in your responses."
)


class GrokChat:
    def __init__(self, api_key: str = "", backend: str = BACKEND_GROQ):
        self.api_key  = api_key
        self.backend  = backend
        self.history  = []
        self._session = None

    def set_api_key(self, key: str):
        self.api_key = key.strip()
        self.history = []

    def set_backend(self, backend: str):
        self.backend = backend
        self.history = []

    def set_session_context(self, context: str):
        self._session = context

    def clear_history(self):
        self.history = []

    def is_groq(self):   return self.backend == BACKEND_GROQ
    def is_grok(self):   return self.backend == BACKEND_GROK
    def is_claude(self): return self.backend == BACKEND_CLAUDE

    def backend_display_name(self):
        return {
            BACKEND_GROQ:   "Groq Cloud",
            BACKEND_GROK:   "Grok (xAI)",
            BACKEND_CLAUDE: "Claude (Anthropic)",
        }.get(self.backend, self.backend)

    # ----------------------------------------------------------------
    # Public send
    # ----------------------------------------------------------------

    def send(self, user_message: str) -> str:
        if not self.api_key:
            urls = {
                BACKEND_GROQ:   "console.groq.com",
                BACKEND_GROK:   "console.x.ai",
                BACKEND_CLAUDE: "console.anthropic.com",
            }
            url = urls.get(self.backend, "your provider's console")
            raise GrokError(
                f"No API key set for {self.backend_display_name()}.\n"
                f"Get your key at {url} and paste it in the Settings tab."
            )
        if self.backend == BACKEND_GROQ:
            return self._send_groq_sdk(user_message)
        if self.backend == BACKEND_CLAUDE:
            return self._send_claude(user_message)
        return self._send_openai_compat(user_message)

    # ----------------------------------------------------------------
    # Groq Cloud via official SDK (no Cloudflare 403)
    # ----------------------------------------------------------------

    def _send_groq_sdk(self, user_message: str) -> str:
        if not GROQ_SDK_AVAILABLE:
            raise GrokError(
                "The 'groq' package is not installed.\n"
                "Run:  pip install groq"
            )

        sys_content = SYSTEM_PROMPT
        if self._session:
            sys_content += f"\n\nCurrent session data:\n{self._session}"

        messages = [{"role": "system", "content": sys_content}]
        messages += self.history
        messages.append({"role": "user", "content": user_message})

        print(f"[AI] Groq Cloud SDK request ({GROQ_MODEL}), key={self.api_key[:8]}...")

        try:
            client = _GroqSDK(api_key=self.api_key)
            completion = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                max_tokens=800,
                temperature=0.7,
            )
            reply = completion.choices[0].message.content or ""
            if not reply:
                raise ValueError("empty response")
        except GrokError:
            raise
        except Exception as ex:
            err = str(ex)
            if "401" in err or "invalid_api_key" in err.lower() or "authentication" in err.lower():
                raise GrokError(
                    "Groq API key is invalid or expired.\n"
                    "Regenerate your key at console.groq.com and paste it in Settings."
                )
            if "403" in err:
                raise GrokError(
                    "Groq returned 403 Forbidden.\n"
                    "Try regenerating your key at console.groq.com."
                )
            raise GrokError(f"Groq Cloud error: {ex}")

        self._push_history(user_message, reply)
        return reply

    # ----------------------------------------------------------------
    # xAI Grok via OpenAI-compatible HTTP
    # ----------------------------------------------------------------

    def _send_openai_compat(self, user_message: str) -> str:
        sys_content = SYSTEM_PROMPT
        if self._session:
            sys_content += f"\n\nCurrent session data:\n{self._session}"

        messages = [{"role": "system", "content": sys_content}]
        messages += self.history
        messages.append({"role": "user", "content": user_message})

        payload = json.dumps({
            "model": GROK_MODEL, "messages": messages,
            "max_tokens": 800, "temperature": 0.7,
        }).encode("utf-8")
        print(f"[AI] Grok (xAI) request ({GROK_MODEL}), key={self.api_key[:8]}...")

        req = urllib.request.Request(GROK_URL, data=payload, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            })
        data = self._do_request(req, "Grok (xAI)")

        try:
            content = data["choices"][0]["message"]["content"]
            reply = content if isinstance(content, str) else \
                    " ".join(b.get("text", "") for b in content if b.get("type") == "text")
            if not reply:
                raise ValueError("empty response")
        except GrokError:
            raise
        except Exception as ex:
            raise GrokError(f"Could not parse Grok response: {ex}")

        self._push_history(user_message, reply)
        return reply

    # ----------------------------------------------------------------
    # Anthropic Claude
    # ----------------------------------------------------------------

    def _send_claude(self, user_message: str) -> str:
        sys_content = SYSTEM_PROMPT
        if self._session:
            sys_content += f"\n\nCurrent session data:\n{self._session}"

        messages = list(self.history)
        messages.append({"role": "user", "content": user_message})

        payload = json.dumps({
            "model": CLAUDE_MODEL, "max_tokens": 800,
            "system": sys_content, "messages": messages,
        }).encode("utf-8")
        print(f"[AI] Claude request, key={self.api_key[:8]}...")

        req = urllib.request.Request(CLAUDE_URL, data=payload, method="POST",
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": CLAUDE_VERSION,
            })
        data = self._do_request(req, "Claude")

        try:
            blocks = data.get("content", [])
            reply  = " ".join(b.get("text", "") for b in blocks
                              if b.get("type") == "text").strip()
            if not reply:
                raise ValueError("empty response")
        except GrokError:
            raise
        except Exception as ex:
            raise GrokError(f"Could not parse Claude response: {ex}")

        self._push_history(user_message, reply)
        return reply

    # ----------------------------------------------------------------
    # Shared HTTP helper
    # ----------------------------------------------------------------

    def _do_request(self, req, label: str) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                print(f"[AI] {label} 200 OK -- {len(raw)} chars")
                return json.loads(raw)
        except urllib.error.HTTPError as ex:
            body = ex.read().decode("utf-8", errors="replace")
            print(f"[AI] {label} HTTP {ex.code}: {body[:300]}")
            try:
                obj = json.loads(body)
                msg = obj.get("error", body)
                if isinstance(msg, dict):
                    msg = msg.get("message", body)
            except Exception:
                msg = body[:250]
            raise GrokError(f"{label} API error {ex.code}: {msg}")
        except urllib.error.URLError as ex:
            raise GrokError(f"Network error: {ex.reason}")
        except Exception as ex:
            raise GrokError(f"Unexpected error: {ex}")

    def _push_history(self, user_msg: str, reply: str):
        self.history.append({"role": "user",      "content": user_msg})
        self.history.append({"role": "assistant",  "content": reply})
        if len(self.history) > 40:
            self.history = self.history[-40:]


class GrokError(Exception):
    pass
