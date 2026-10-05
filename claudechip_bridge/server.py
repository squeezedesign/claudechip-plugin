"""Bridge server.

One small asyncio server on a single port (standard library only):
  GET  /ws            WebSocket for the Claude Chip device (LAN), paired and signed
  POST /pair, /revoke pairing commands from this Mac (loopback only)
  POST /hook/{event}  Claude Code HTTP hooks (loopback only)
  POST /statusline    status line data: context and plan usage (loopback only)
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import hmac
import os
import secrets
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .http import Request, Response
from .http import start as start_http
from .websocket import WebSocket, is_upgrade

from . import describe
from .config import Config
from .context import context_percent
from .identity import DATA_DIR, Devices, bridge_id, sign
from .permissions import Permissions
from .sessions import ASK, DONE, ERR, PERM, WORK, SessionStore
from .summary import clip, summarize

log = logging.getLogger("bridge")

LOOPBACK = {"127.0.0.1", "::1"}
STATE_DEBOUNCE_S = 0.2  # coalesce bursts of hook events into one state message
PAIR_TTL_S = 300         # a pairing code is valid for 5 minutes
LIVENESS_EVERY_S = 15   # how often to check that session processes are alive
CONTEXT_EVERY_S = 10    # read the transcript for CTX at most this often per session
STATE_FILE = DATA_DIR / "sessions.json"
USAGE_POLL_S = 120           # direct usage reads (usage_source = "auto")
USAGE_FRESH_S = 600          # while direct reads work, ignore the status line


@dataclass
class _Conn:
    """One device WebSocket."""
    ws: WebSocket
    peer: str
    nonce: str              # random per connection: part of every signature
    device_id: str = ""
    token: str | None = None  # set once authenticated
    seq: int = 0            # last accepted message counter


class Bridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bridge_id = bridge_id()
        self.paired = Devices()
        self.conns: dict[WebSocket, _Conn] = {}
        # Pending pairings: code -> (connection, expiry), None if ambiguous
        self.pairing: dict[str, tuple[_Conn, float] | None] = {}
        self.sessions = SessionStore()
        self.perms = Permissions()
        self.devices: set[WebSocket] = set()
        # Plan usage from the status line: {"five_hour": (pct, resets_at), ...}
        self.usage: dict[str, tuple[float, float]] = {}
        # Claude Code process of each session, sent by the hooks as X-Claude-Pid
        self.pids: dict[str, int] = {}
        self._context_read_at: dict[str, float] = {}  # last CTX read per session
        self._direct_usage_at = 0.0  # last successful direct usage read
        self._state_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def handle(self, req: Request) -> Optional[Response]:
        """GET /ws for the device (LAN); everything else only from this Mac."""
        if req.path == "/ws" and req.method == "GET" and is_upgrade(req):
            await self.handle_ws(req)
            return None
        if req.method != "POST":
            return Response(404)
        if req.remote not in LOOPBACK:
            return Response(403)
        try:
            data = req.json()
        except ValueError:
            return Response(400)
        if req.path.startswith("/hook/"):
            return await self.handle_hook(req, req.path[len("/hook/"):], data)
        routes = {"/statusline": self.handle_statusline, "/pair": self.handle_pair,
                  "/revoke": self.handle_revoke}
        handler = routes.get(req.path)
        return await handler(data) if handler else Response(404)

    # ------------------------------------------------------------------
    # Messages to the device
    # ------------------------------------------------------------------

    def hello_msg(self) -> dict:
        return {"type": "hello", "proto": 2, "bridge": self.bridge_id, "account": self.cfg.account,
                "mac": self.cfg.mac, "color": self.cfg.color}

    def _update_usage(self, window: str, pct: float, resets_at: float) -> bool:
        """Keep the freshest reading. Within one window usage only grows, so an
        idle session reporting an older, lower value must not overwrite it;
        a later resets_at means a new window and replaces it."""
        old = self.usage.get(window)
        if old is not None:
            old_pct, old_reset = old
            same_window = abs(resets_at - old_reset) < 60
            if (same_window and pct <= old_pct) or resets_at < old_reset - 60:
                return False
        self.usage[window] = (pct, resets_at)
        return True

    def _usage_pct(self, window: str) -> int:
        """Percentage of a plan window, -1 when unknown, 0 once it has reset."""
        if window not in self.usage:
            return -1
        pct, resets_at = self.usage[window]
        if resets_at and time.time() >= resets_at:
            return 0
        return round(pct)

    def state_msg(self) -> dict:
        return {
            "type": "state",
            "account": self.cfg.account,
            "usage": {"ses": self._usage_pct("five_hour"), "sem": self._usage_pct("seven_day")},
            "sessions": [s.to_json() for s in self.sessions.all()],
        }

    async def broadcast(self, msg: dict) -> None:
        data = json.dumps(msg, ensure_ascii=False)
        for ws in list(self.devices):
            try:
                await ws.send_str(data)
            except ConnectionError:
                self.devices.discard(ws)

    async def flush_state(self) -> None:
        """Send the state right now. Used before events that refer to a session
        (permission, summary) so the device always knows it first."""
        if self._state_task and not self._state_task.done():
            self._state_task.cancel()
        await self.broadcast(self.state_msg())

    def schedule_state(self) -> None:
        """Send a state message soon, merging changes that arrive together."""
        if self._state_task and not self._state_task.done():
            return

        async def later() -> None:
            await asyncio.sleep(STATE_DEBOUNCE_S)
            await self.broadcast(self.state_msg())
            self.save_sessions()

        self._state_task = asyncio.create_task(later())

    # ------------------------------------------------------------------
    # Device WebSocket: pairing and authentication (SPEC §7.3)
    # ------------------------------------------------------------------

    async def handle_ws(self, request: Request) -> None:
        ws = await WebSocket.accept(request)
        heartbeat = asyncio.ensure_future(ws.heartbeat())  # notices a device that vanished
        conn = _Conn(ws=ws, peer=request.remote or "?", nonce=secrets.token_hex(16))
        try:
            # Who we are, before any authentication: nothing secret in here
            await ws.send_json({**self.hello_msg(), "nonce": conn.nonce})
            while True:
                text = await ws.recv()
                if text is None:
                    break
                try:
                    data = json.loads(text)
                except ValueError:
                    continue
                if conn.token:
                    await self._on_signed(conn, data)
                else:
                    await self._on_unauthenticated(conn, data)
        except ConnectionError:
            pass
        finally:
            heartbeat.cancel()
            await ws.close()
            self.devices.discard(ws)
            self.conns.pop(ws, None)
            for code in [c for c, entry in self.pairing.items() if entry and entry[0] is conn]:
                del self.pairing[code]
            log.info("device %s disconnected", conn.device_id or conn.peer)

    async def _on_unauthenticated(self, conn: "_Conn", data: dict) -> None:
        kind = data.get("type")
        device_id = str(data.get("device", ""))[:32]
        if kind == "auth":
            # The device proves it knows its token without sending it
            token = self.paired.token(device_id)
            device_nonce = str(data.get("nonce", ""))
            expected = sign(token, f"auth:{conn.nonce}:{device_nonce}:{device_id}") if token else ""
            if not token or not hmac.compare_digest(expected, str(data.get("mac", ""))):
                log.warning("device %s (%s): authentication failed", device_id or "?", conn.peer)
                await conn.ws.send_json({"type": "auth_fail"})
                return
            conn.token, conn.device_id = token, device_id
            # And the bridge proves it too, so the device knows it is talking to us
            await conn.ws.send_json({"type": "auth_ok", "mac": sign(token, f"ok:{device_nonce}:{conn.nonce}")})
            self.devices.add(conn.ws)
            self.conns[conn.ws] = conn
            log.info("device %s connected (%s)", device_id, conn.peer)
            await conn.ws.send_json(self.state_msg())
            for p in self.perms.all():  # requests that arrived while it was away
                await conn.ws.send_json(p.to_json())
        elif kind == "pair_request":
            code = str(data.get("code", ""))
            if len(code) != 6 or not code.isdigit() or not device_id:
                return
            conn.device_id = device_id
            existing = self.pairing.get(code)
            # Two devices showing the same code: refuse both rather than guess
            self.pairing[code] = None if existing and existing[0] is not conn else (conn, time.time() + PAIR_TTL_S)
            # The code is not logged on purpose: it must be read on the device itself
            log.info("device %s asks to pair: run 'python3 -m claudechip_bridge pair <code on its screen>'", device_id)

    async def _on_signed(self, conn: "_Conn", data: dict) -> None:
        """After authentication every message is {"seq", "msg", "sig"}: sig is the
        HMAC of "<connection nonce>:<seq>:<msg>" and seq always grows, so a
        captured message can be neither forged nor replayed."""
        try:
            seq, body, sig = int(data["seq"]), str(data["msg"]), str(data["sig"])
        except (KeyError, TypeError, ValueError):
            log.warning("device %s: unsigned message dropped", conn.device_id)
            return
        if seq <= conn.seq or not hmac.compare_digest(sign(conn.token, f"{conn.nonce}:{seq}:{body}"), sig):
            log.warning("device %s: bad signature or replayed message dropped", conn.device_id)
            return
        conn.seq = seq
        try:
            await self.on_device_message(json.loads(body))
        except ValueError:
            pass

    async def handle_pair(self, data: dict) -> Response:
        """POST /pair {"code"}: from 'claudechip-bridge pair <code>' on this Mac."""
        code = str(data.get("code", "")).replace(" ", "")
        now = time.time()
        for c in [c for c, e in self.pairing.items() if e and e[1] < now]:
            del self.pairing[c]
        if code not in self.pairing:
            return Response.json({"ok": False, "error": "no device is showing that code (or it expired)"})
        entry = self.pairing.pop(code)
        if entry is None:
            return Response.json({"ok": False, "error": "two devices showed that code; start again"})
        conn = entry[0]
        token = self.paired.pair(conn.device_id)
        await conn.ws.send_json({"type": "paired", "token": token})
        log.info("device %s paired", conn.device_id)
        return Response.json({"ok": True, "device": conn.device_id})

    async def handle_revoke(self, data: dict) -> Response:
        """POST /revoke {"device"}: forget a device and drop its connection."""
        full = self.paired.revoke(str(data.get("device", "")))
        if not full:
            return Response.json({"ok": False, "error": "no single device matches that id"})
        for conn in [c for c in self.conns.values() if c.device_id == full]:
            await conn.ws.send_json({"type": "revoked"})
            await conn.ws.close(4401, b"revoked")
        log.info("device %s revoked", full)
        return Response.json({"ok": True, "device": full})

    async def on_device_message(self, data: dict) -> None:
        kind = data.get("type")
        if kind == "decision":
            request_id = str(data.get("request_id", ""))
            allow = bool(data.get("allow"))
            if self.perms.resolve(request_id, allow):
                log.info("decision %s: %s (from device)", request_id, "allow" if allow else "deny")
            else:
                log.info("decision %s ignored: already answered", request_id)
        elif kind in ("select", "new_session"):
            log.info("%s not supported yet (phase 5): %s", kind, data)
        else:
            log.debug("unknown device message: %s", data)

    # ------------------------------------------------------------------
    # Claude Code hooks
    # ------------------------------------------------------------------

    async def handle_hook(self, request: Request, event: str, data: dict) -> Response:
        pid = request.headers.get("x-claude-pid", "")
        if pid.isdigit() and data.get("session_id"):
            self.pids[data["session_id"]] = int(pid)
        project = request.headers.get("x-claude-project", "")
        if project:
            data["project_dir"] = project

        handler = getattr(self, f"on_{event}", None)
        if handler is None:
            return Response()  # empty 2xx: no decision, nothing to do
        if event == "PermissionRequest":
            result = await handler(data, request)
        else:
            result = await handler(data)
        return Response.json(result) if result else Response()

    def _session(self, data: dict):
        session_id = data.get("session_id", "?")
        project = data.get("project_dir", "")
        s = self.sessions.get_or_create(session_id, project or data.get("cwd", ""))
        if project:
            self.sessions.set_project(session_id, project)
        return s

    async def on_SessionStart(self, data: dict) -> None:
        s = self._session(data)
        self.sessions.update(s, status=DONE, text="sesión lista. ¿qué hacemos?")
        log.info("[%s] session started (%s)", s.name, data.get("source", "?"))
        self.schedule_state()

    async def on_SessionEnd(self, data: dict) -> None:
        await self._end_session(data.get("session_id", ""), "session ended")

    async def _end_session(self, session_id: str, reason: str) -> None:
        self.pids.pop(session_id, None)
        s = self.sessions.remove(session_id)
        if s:
            log.info("[%s] %s", s.name, reason)
            for p in self.perms.for_session(s.id):
                await self._cancel_permission(p)
            self.schedule_state()

    # ------------------------------------------------------------------
    # Persistence: sessions survive a bridge restart
    # ------------------------------------------------------------------

    def save_sessions(self) -> None:
        data = [{**s.to_json(), "cwd": s.cwd, "pid": self.pids.get(s.id)}
                for s in self.sessions.all() if s.id in self.pids]
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, ensure_ascii=False))
        except OSError as e:
            log.warning("could not save sessions: %s", e)

    def load_sessions(self) -> None:
        """Restore sessions whose Claude Code process is still running. A pending
        permission cannot be restored (its hook died with the old bridge)."""
        try:
            data = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return
        for item in data:
            pid = item.get("pid")
            if not pid or not _alive(pid):
                continue
            s = self.sessions.get_or_create(item["id"], item.get("cwd", ""))
            status = item.get("status", DONE)
            self.sessions.update(s, status=WORK if status == PERM else status, text=item.get("text", ""))
            s.ctx = item.get("ctx", 0)
            self.pids[s.id] = pid
        if self.sessions.all():
            log.info("restored %d session(s): %s", len(self.sessions.all()),
                     ", ".join(s.name for s in self.sessions.all()))

    async def poll_usage(self) -> None:
        """Read plan usage straight from Anthropic every few minutes."""
        from . import oauth_usage

        warned = False
        while True:
            try:
                login = await asyncio.to_thread(oauth_usage.read_login)
                usage = await asyncio.to_thread(oauth_usage.fetch_usage, login)
            except oauth_usage.UsageError as e:
                if not warned:
                    log.info("direct usage unavailable (%s); using the status line", e)
                    warned = True
            else:
                warned = False
                self._direct_usage_at = time.time()
                if any(self.usage.get(w) != v for w, v in usage.items()):
                    self.usage.update(usage)
                    self.schedule_state()
            await asyncio.sleep(USAGE_POLL_S)

    async def watch_liveness(self) -> None:
        """Drop sessions whose Claude Code process is gone without a SessionEnd
        (crash, window reload, killed terminal)."""
        while True:
            await asyncio.sleep(LIVENESS_EVERY_S)
            for session_id, pid in list(self.pids.items()):
                if not _alive(pid):
                    await self._end_session(session_id, f"session gone (process {pid} ended)")

    async def on_UserPromptSubmit(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        prompt = data.get("prompt") or data.get("message") or ""
        self.sessions.update(s, status=WORK, text=clip(f"pensando: {prompt}") if prompt else "pensando...")
        self.schedule_state()

    async def on_PreToolUse(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        if s.status == PERM:
            return
        tool, tool_input = data.get("tool_name", ""), data.get("tool_input") or {}
        waiting = describe.waiting_text(tool, tool_input)
        if waiting:
            # Plan approval or a question: Claude stops until the user answers
            await self._ask(s, waiting)
        else:
            self.sessions.update(s, status=WORK, text=describe.activity(tool, tool_input))
            self.schedule_state()

    async def _ask(self, s, text: str) -> None:
        """Claude waits for the user in the console: call attention on the device."""
        if s.status == ASK and s.text == text:
            return
        self.sessions.update(s, status=ASK, text=text)
        log.info("[%s] waiting for the user: %s", s.name, text)
        await self.flush_state()
        await self.broadcast({"type": "summary", "session_id": s.id, "status": ASK, "text": text})

    async def on_PostToolUse(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        now = time.time()
        if now - self._context_read_at.get(s.id, 0) >= CONTEXT_EVERY_S:
            self._context_read_at[s.id] = now
            await self._update_context(s, data)
        if s.status in (PERM, DONE, ASK):
            self.sessions.update(s, status=WORK)
            self.schedule_state()

    on_PostToolUseFailure = on_PostToolUse

    async def on_PermissionRequest(self, data: dict, request: Request) -> Optional[dict]:
        s = self._session(data)
        text = describe.permission_question(data.get("tool_name", ""), data.get("tool_input") or {})
        p = self.perms.create(s.id, data.get("tool_use_id", ""), text)
        self.sessions.update(s, status=PERM, text=text)
        log.info("[%s] permission %s: %s", s.name, p.request_id, text)
        await self.flush_state()
        await self.broadcast(p.to_json())

        # No timeout: device or terminal, first one wins. If Claude Code drops the
        # hook (session closed, hook killed) the connection closes.
        gone = asyncio.ensure_future(request.wait_disconnect())
        done, _ = await asyncio.wait({p.future, gone}, return_when=asyncio.FIRST_COMPLETED)
        gone.cancel()
        if p.future not in done:
            await self._cancel_permission(p)
            return None
        allow = p.future.result()
        if allow is None:
            return None  # answered in the terminal: release the hook silently

        self.sessions.update(s, status=WORK, text="permiso concedido. ejecutando..." if allow
                             else "vale, no lo ejecuto. busco otra vía.")
        self.schedule_state()
        decision = {"behavior": "allow" if allow else "deny"}
        if not allow:
            decision["message"] = "Denegado desde Claude Chip"
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}

    async def _answered_elsewhere(self, data: dict) -> None:
        """Any later event of a session means its pending permission was
        answered in the terminal. The PreToolUse of the same tool call is the
        exception: it is async and can arrive just after the request."""
        session_id = data.get("session_id", "")
        tool_use_id = data.get("tool_use_id")
        for p in self.perms.for_session(session_id):
            if data.get("hook_event_name") == "PreToolUse" and tool_use_id == p.tool_use_id:
                continue
            s = self.sessions.get(session_id)
            log.info("[%s] permission %s answered in the terminal", s.name if s else "?", p.request_id)
            await self._cancel_permission(p)

    async def _cancel_permission(self, p) -> None:
        self.perms.resolve(p.request_id, None)  # release the waiting hook
        self.perms.discard(p.request_id)
        await self.broadcast({"type": "permission_cancel", "request_id": p.request_id})
        s = self.sessions.get(p.session_id)
        if s and s.status == PERM:
            self.sessions.update(s, status=WORK, text="sigo trabajando...")
        self.schedule_state()

    async def _update_context(self, s, data: dict) -> None:
        """CTX from the transcript: works for VS Code sessions too, which send
        no status line. Read in a thread, the file can be large."""
        path = data.get("transcript_path")
        if not path:
            return
        pct = await asyncio.to_thread(context_percent, path)
        if pct is not None and pct != s.ctx:
            s.ctx = pct
            self.schedule_state()

    async def on_Stop(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        await self._update_context(s, data)
        text = summarize(data.get("last_assistant_message", ""), self.cfg.summary)
        self.sessions.update(s, status=DONE, text=text)
        log.info("[%s] done: %s", s.name, text)
        await self.flush_state()
        await self.broadcast({"type": "summary", "session_id": s.id, "status": DONE, "text": text})

    async def on_StopFailure(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        error = data.get("error") or data.get("message") or "fallo de la API"
        text = clip(f"error: {error}")
        self.sessions.update(s, status=ERR, text=text)
        log.info("[%s] error: %s", s.name, text)
        await self.flush_state()
        await self.broadcast({"type": "summary", "session_id": s.id, "status": ERR, "text": text})

    # Notifications that mean "Claude is waiting for you", with the Spanish text
    # shown on the device (Claude Code's own messages are in English).
    # idle_prompt is left out on purpose: a finished answer already shows as done.
    _WAITING_NOTIFICATIONS = {
        "permission_prompt": "necesito permiso. mira la consola.",
        "elicitation_dialog": "una herramienta pide datos. mira la consola.",
        "agent_needs_input": "te espero en la consola.",
    }

    async def on_Notification(self, data: dict) -> None:
        kind = data.get("notification_type") or data.get("type") or ""
        message = data.get("message", "")
        log.info("notification %s: %s", kind or "?", message)
        if kind not in self._WAITING_NOTIFICATIONS:
            return
        s = self._session(data)
        # A permission the device can answer is already on screen
        if s.status == PERM or self.perms.for_session(s.id):
            return
        await self._ask(s, self._WAITING_NOTIFICATIONS[kind])

    # ------------------------------------------------------------------
    # Status line: context window and plan usage
    # ------------------------------------------------------------------

    async def handle_statusline(self, data: dict) -> Response:

        changed = False
        direct_fresh = time.time() - self._direct_usage_at < USAGE_FRESH_S
        session_id = data.get("session_id")
        if session_id:
            ws = data.get("workspace") or {}
            project = ws.get("project_dir") or ""
            s = self.sessions.get_or_create(session_id, project or ws.get("current_dir") or data.get("cwd", ""))
            if project:
                self.sessions.set_project(session_id, project)
            ctx = (data.get("context_window") or {}).get("used_percentage")
            if ctx is not None and round(ctx) != s.ctx:
                s.ctx = round(ctx)
                changed = True

        for window, info in (data.get("rate_limits") or {}).items():
            if direct_fresh:
                break  # the direct reading is authoritative
            if window in ("five_hour", "seven_day") and info.get("used_percentage") is not None:
                if self._update_usage(window, float(info["used_percentage"]),
                                      float(info.get("resets_at") or 0)):
                    changed = True

        if changed:
            self.schedule_state()
        return Response()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # exists, owned by someone else
    return True


async def serve(cfg: Config) -> None:
    from .mdns import Advertiser

    bridge = Bridge(cfg)
    bridge.load_sessions()
    try:
        server = await start_http(bridge.handle, "0.0.0.0", cfg.port)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"port {cfg.port} is already in use: is another bridge running?\n"
                f"find it with: lsof -nP -iTCP:{cfg.port} -sTCP:LISTEN"
            ) from None
        raise

    advertiser = Advertiser(cfg)
    await advertiser.start()
    tasks = [asyncio.ensure_future(bridge.watch_liveness())]
    if cfg.usage_source == "auto":
        tasks.append(asyncio.ensure_future(bridge.poll_usage()))
    log.info("bridge %s listening on port %d (mDNS %s)", cfg.account, cfg.port, advertiser.name)
    # Stop cleanly on kill / logout too, not only Ctrl+C: dns-sd must not
    # keep announcing a bridge that is gone
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
        log.info("bridge stopping")
    finally:
        for task in tasks:
            task.cancel()
        await advertiser.stop()
        server.close()
