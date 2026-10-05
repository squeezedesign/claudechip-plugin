"""Bridge server.

One aiohttp app on a single port:
  GET  /ws            WebSocket for the Claude Chip device (LAN)
  POST /hook/{event}  Claude Code HTTP hooks (loopback only)
  POST /statusline    status line data: context and plan usage (loopback only)
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from . import describe
from .config import Config
from .permissions import Permissions
from .sessions import ASK, DONE, ERR, PERM, WORK, SessionStore
from .summary import clip, summarize

log = logging.getLogger("bridge")

LOOPBACK = {"127.0.0.1", "::1"}
STATE_DEBOUNCE_S = 0.2  # coalesce bursts of hook events into one state message
AUTH_TIMEOUT_S = 5
LIVENESS_EVERY_S = 15   # how often to check that session processes are alive
STATE_FILE = Path.home() / ".config" / "claudechip" / "sessions.json"


class Bridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.sessions = SessionStore()
        self.perms = Permissions()
        self.devices: set[web.WebSocketResponse] = set()
        # Plan usage from the status line: {"five_hour": (pct, resets_at), ...}
        self.usage: dict[str, tuple[float, float]] = {}
        # Claude Code process of each session, sent by the hooks as X-Claude-Pid
        self.pids: dict[str, int] = {}
        self._state_task: asyncio.Task | None = None

        self.app = web.Application()
        self.app.add_routes([
            web.get("/ws", self.handle_ws),
            web.post("/hook/{event}", self.handle_hook),
            web.post("/statusline", self.handle_statusline),
        ])

    # ------------------------------------------------------------------
    # Messages to the device
    # ------------------------------------------------------------------

    def hello_msg(self) -> dict:
        return {"type": "hello", "account": self.cfg.account, "mac": self.cfg.mac,
                "color": self.cfg.color}

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
    # Device WebSocket
    # ------------------------------------------------------------------

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        peer = request.remote

        # The first message must be a hello carrying the token
        try:
            first = await ws.receive(timeout=AUTH_TIMEOUT_S)
            hello = json.loads(first.data) if first.type == WSMsgType.TEXT else {}
        except (asyncio.TimeoutError, ValueError):
            hello = {}
        if hello.get("type") != "hello" or not self._token_ok(hello):
            log.warning("device %s rejected: bad or missing token", peer)
            await ws.close(code=4401, message=b"unauthorized")
            return ws

        log.info("device %s connected (%s)", peer, hello.get("device", "?"))
        self.devices.add(ws)
        await ws.send_json(self.hello_msg())
        await ws.send_json(self.state_msg())
        for p in self.perms.all():  # requests that arrived while it was away
            await ws.send_json(p.to_json())

        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if not self._token_ok(data):
                    log.warning("device %s: message without a valid token dropped", peer)
                    continue
                await self.on_device_message(data)
        finally:
            self.devices.discard(ws)
            log.info("device %s disconnected", peer)
        return ws

    def _token_ok(self, data: dict) -> bool:
        return data.get("token") == self.cfg.dev_token

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

    async def handle_hook(self, request: web.Request) -> web.Response:
        if request.remote not in LOOPBACK:
            raise web.HTTPForbidden()
        event = request.match_info["event"]
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest()
        pid = request.headers.get("X-Claude-Pid", "")
        if pid.isdigit() and data.get("session_id"):
            self.pids[data["session_id"]] = int(pid)
        project = request.headers.get("X-Claude-Project", "")
        if project:
            data["project_dir"] = project

        handler = getattr(self, f"on_{event}", None)
        if handler is None:
            return web.Response()  # empty 2xx: no decision, nothing to do
        result = await handler(data)
        return web.json_response(result) if result else web.Response()

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
        await self.broadcast({"type": "summary", "session_id": s.id, "status": ASK, "text": text})
        self.schedule_state()

    async def on_PostToolUse(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        if s.status in (PERM, DONE, ASK):
            self.sessions.update(s, status=WORK)
            self.schedule_state()

    on_PostToolUseFailure = on_PostToolUse

    async def on_PermissionRequest(self, data: dict) -> dict | None:
        s = self._session(data)
        text = describe.permission_question(data.get("tool_name", ""), data.get("tool_input") or {})
        p = self.perms.create(s.id, data.get("tool_use_id", ""), text)
        self.sessions.update(s, status=PERM, text=text)
        log.info("[%s] permission %s: %s", s.name, p.request_id, text)
        await self.broadcast(p.to_json())
        self.schedule_state()

        try:
            allow = await p.future  # no timeout: device or terminal, first one wins
        except asyncio.CancelledError:
            # Claude Code dropped the hook (session closed or hook killed)
            await self._cancel_permission(p)
            raise
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

    async def on_Stop(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        text = summarize(data.get("last_assistant_message", ""), self.cfg.summary)
        self.sessions.update(s, status=DONE, text=text)
        log.info("[%s] done: %s", s.name, text)
        await self.broadcast({"type": "summary", "session_id": s.id, "status": DONE, "text": text})
        self.schedule_state()

    async def on_StopFailure(self, data: dict) -> None:
        await self._answered_elsewhere(data)
        s = self._session(data)
        error = data.get("error") or data.get("message") or "fallo de la API"
        text = clip(f"error: {error}")
        self.sessions.update(s, status=ERR, text=text)
        log.info("[%s] error: %s", s.name, text)
        await self.broadcast({"type": "summary", "session_id": s.id, "status": ERR, "text": text})
        self.schedule_state()

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

    async def handle_statusline(self, request: web.Request) -> web.Response:
        if request.remote not in LOOPBACK:
            raise web.HTTPForbidden()
        try:
            data = await request.json()
        except ValueError:
            raise web.HTTPBadRequest()

        changed = False
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
            if window in ("five_hour", "seven_day") and info.get("used_percentage") is not None:
                value = (float(info["used_percentage"]), float(info.get("resets_at") or 0))
                if self.usage.get(window) != value:
                    self.usage[window] = value
                    changed = True

        if changed:
            self.schedule_state()
        return web.Response()


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
    # handler_cancellation: when Claude Code drops a pending PermissionRequest
    # (answered in the terminal), the handler is cancelled and we can tell the device.
    runner = web.AppRunner(bridge.app, handler_cancellation=True, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=cfg.port)
    try:
        await site.start()
    except OSError as e:
        await runner.cleanup()
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(
                f"port {cfg.port} is already in use: is another bridge running?\n"
                f"find it with: lsof -nP -iTCP:{cfg.port} -sTCP:LISTEN"
            ) from None
        raise

    advertiser = Advertiser(cfg)
    await advertiser.start()
    liveness = asyncio.create_task(bridge.watch_liveness())
    log.info("bridge %s listening on port %d (mDNS %s)", cfg.account, cfg.port, advertiser.name)
    try:
        await asyncio.Event().wait()
    finally:
        liveness.cancel()
        await advertiser.stop()
        await runner.cleanup()
