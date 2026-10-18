"""HTTP API around the clinic agent.

    POST   /v1/sessions                    start a chat -> {session_id}
    POST   /v1/sessions/{id}/messages      send a patient message -> agent reply
    DELETE /v1/sessions/{id}               end a chat
    GET    /healthz   liveness: the process is up
    GET    /readyz    readiness: policy loaded and model client configured
    GET    /version   commit, policy version and model provider that are running
    GET    /metrics   Prometheus metrics

Run with:  uvicorn service.app:create_app --factory --host 0.0.0.0 --port 8000

Each chat holds its own in-memory clinic World (the demo database), so sessions live in one pod's memory.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from agent.agent import Agent
from agent.llm import InfraError
from agent.policy import latest_version, load
from agent.world import World

from . import metrics as M
from .config import Settings
from .logs import setup_logging
from .tracing import setup_tracing, tracer

log = logging.getLogger("clinic.api")


class InstrumentedLLM:
    """Wraps the model client to record latency, outcome and a trace span for every call."""

    def __init__(self, inner, provider: str):
        self.inner, self.provider = inner, provider

    def chat(self, *args, **kwargs):
        start, outcome = time.perf_counter(), "error"
        with tracer.start_as_current_span("llm.chat", attributes={"llm.provider": self.provider}) as span:
            try:
                resp = self.inner.chat(*args, **kwargs)
                outcome = "blocked" if resp.blocked else ("tool_call" if resp.tool_calls else "text")
                return resp
            except InfraError:
                outcome = "infra_error"
                raise
            finally:
                span.set_attribute("llm.outcome", outcome)
                M.LLM_CALLS.labels(self.provider, outcome).inc()
                M.LLM_LATENCY.labels(self.provider).observe(time.perf_counter() - start)


def build_llm(settings: Settings):
    """Returns (llm, not_ready_reason). A missing key keeps the pod un-ready instead of crashing it."""
    if settings.llm_provider == "scripted":
        from .scripted_llm import ScriptedLLM
        return InstrumentedLLM(ScriptedLLM(), "scripted"), None
    if settings.llm_provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            return None, "GEMINI_API_KEY is not set (create the Kubernetes secret)"
        from agent.llm import GeminiLLM
        return InstrumentedLLM(GeminiLLM(), "gemini"), None
    raise ValueError(f"unknown LLM_PROVIDER {settings.llm_provider!r} (use gemini or scripted)")


class ChatSession:
    def __init__(self, policy: dict, llm):
        self.world = World()
        self.agent = Agent(self.world, policy, llm)
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        self.audit_seen = 0
        self.handoffs_seen = 0


class SessionStore:
    def __init__(self, ttl: int, max_sessions: int):
        self.ttl, self.max = ttl, max_sessions
        self._s: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for sid in [k for k, v in self._s.items() if v.last_used < cutoff]:
            del self._s[sid]

    def create(self, policy, llm) -> str | None:
        with self._lock:
            self._expire()
            if len(self._s) >= self.max:
                return None
            sid = uuid.uuid4().hex
            self._s[sid] = ChatSession(policy, llm)
            M.ACTIVE_SESSIONS.set(len(self._s))
            return sid

    def get(self, sid: str) -> ChatSession | None:
        with self._lock:
            self._expire()
            M.ACTIVE_SESSIONS.set(len(self._s))
            return self._s.get(sid)

    def delete(self, sid: str) -> bool:
        with self._lock:
            found = self._s.pop(sid, None) is not None
            M.ACTIVE_SESSIONS.set(len(self._s))
            return found


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _record_new_activity(sess: ChatSession) -> dict:
    """Turn the agent's new audit rows into metrics and span events. Reads state, not what the agent said."""
    rows = sess.world.qa("SELECT id, tool, ok, committed, is_write FROM audit WHERE id > ? ORDER BY id",
                         sess.audit_seen)
    span = trace.get_current_span()
    for r in rows:
        M.TOOL_CALLS.labels(r["tool"], "true" if r["ok"] else "false").inc()
        if r["is_write"] and r["committed"]:
            M.WRITES_COMMITTED.labels(r["tool"]).inc()
        span.add_event("tool_call", {"tool": r["tool"], "ok": bool(r["ok"]), "committed": bool(r["committed"])})
        sess.audit_seen = r["id"]
    handoffs = sess.world.qa("SELECT id, urgency FROM handoffs WHERE id > ? ORDER BY id", sess.handoffs_seen)
    for h in handoffs:
        M.ESCALATIONS.labels(h["urgency"]).inc()
        sess.handoffs_seen = h["id"]
    committed = sess.world.q1("SELECT COUNT(*) c FROM audit WHERE committed=1 AND is_write=1")["c"]
    return {"tool_calls": len(rows), "writes_committed": committed, "escalated": sess.agent.session.escalated}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    setup_logging(settings.log_level)

    version = latest_version() if settings.policy_version == "latest" else int(settings.policy_version)
    policy = load(version)
    llm, not_ready = build_llm(settings)

    app = FastAPI(title="clinic-agent", version=settings.app_version)
    app.state.settings, app.state.policy, app.state.llm, app.state.not_ready = settings, policy, llm, not_ready
    app.state.sessions = SessionStore(settings.session_ttl_seconds, settings.max_sessions)
    traced = setup_tracing(app, settings.app_version)
    M.BUILD.info({"version": settings.app_version, "policy": str(policy["version"]), "provider": settings.llm_provider})
    log.info("starting", extra={"event": "startup", "version": settings.app_version, "policy": policy["version"],
                                "provider": settings.llm_provider, "outcome": "ready" if not not_ready else not_ready})
    if traced:
        log.info("tracing enabled", extra={"event": "tracing"})

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            dur = time.perf_counter() - start
            M.HTTP_REQUESTS.labels(route, request.method, str(status)).inc()
            M.HTTP_LATENCY.labels(route).observe(dur)
            if route not in ("/healthz", "/readyz", "/metrics"):
                log.info("request", extra={"request_id": rid, "route": route, "method": request.method,
                                           "status": status, "duration_ms": round(dur * 1000, 1)})

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        if app.state.not_ready:
            return JSONResponse({"ready": False, "reason": app.state.not_ready}, status_code=503)
        return {"ready": True}

    @app.get("/version")
    def version_info():
        return {"version": settings.app_version, "policy_version": policy["version"],
                "llm_provider": settings.llm_provider}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/v1/sessions", status_code=201)
    def create_session():
        if app.state.not_ready:
            raise HTTPException(503, detail=app.state.not_ready)
        sid = app.state.sessions.create(policy, llm)
        if sid is None:
            raise HTTPException(503, detail="too many active chats, try again shortly", headers={"Retry-After": "30"})
        log.info("session created", extra={"event": "session_created", "session_id": sid})
        return {"session_id": sid, "policy_version": policy["version"]}

    @app.post("/v1/sessions/{session_id}/messages")
    def send_message(session_id: str, body: MessageIn):
        sess = app.state.sessions.get(session_id)
        if sess is None:
            raise HTTPException(404, detail="unknown or expired session")
        with sess.lock:
            sess.last_used = time.monotonic()
            with tracer.start_as_current_span("agent.respond", attributes={"session.turn": sess.agent.session.turn + 1}):
                try:
                    reply = sess.agent.respond(body.text)
                except InfraError as e:
                    log.warning("model unavailable", extra={"event": "llm_unavailable", "session_id": session_id,
                                                            "error": str(e)[:200]})
                    raise HTTPException(503, detail="the assistant is temporarily unavailable",
                                        headers={"Retry-After": "10"}) from e
                activity = _record_new_activity(sess)
        log.info("turn", extra={"event": "turn", "session_id": session_id,
                                "outcome": "escalated" if activity["escalated"] else "ok"})
        return {"reply": reply, "turn": sess.agent.session.turn, **activity}

    @app.delete("/v1/sessions/{session_id}", status_code=204)
    def end_session(session_id: str):
        if not app.state.sessions.delete(session_id):
            raise HTTPException(404, detail="unknown or expired session")
        return Response(status_code=204)

    return app

