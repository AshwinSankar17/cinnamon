"""HTTP + WebSocket API (protocol/API.md)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__
from .core import Brain
from .identity import AuthError, host_of


class PairComplete(BaseModel):
    code: str
    device_pub: str
    device_name: str
    nonce_device: str
    sig: str
    mac: str


class Challenge(BaseModel):
    device_id: str


class TokenReq(BaseModel):
    device_id: str
    nonce: str
    sig: str


class RefreshReq(BaseModel):
    device_id: str
    refresh_token: str


class NewThread(BaseModel):
    repo_id: str
    title: str = ""
    mode: str = "readonly"
    provider: str | None = None
    base_ref: str | None = None


class PatchThread(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    muted: bool | None = None


class ForkReq(BaseModel):
    title: str
    mode: str = "worktree"
    at_seq: int | None = None


class MessageReq(BaseModel):
    text: str


class DecisionReq(BaseModel):
    decision: str
    note: str | None = None
    device_sig: str


class PushToken(BaseModel):
    fcm_token: str


class HookReq(BaseModel):
    thread_id: str
    tool_name: str | None = None
    tool_input: dict[str, Any] = {}
    tool_use_id: str | None = None
    session_id: str | None = None
    cwd: str | None = None


class SubmitReq(BaseModel):
    thread_id: str
    script: str
    sbatch_args: list[str] = []


def create_app(brain: Brain, *, dev_no_auth: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        brain.events.bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="Cluster Brain", version=__version__, lifespan=lifespan)

    def auth(authorization: str = Header(default="")) -> str:
        if dev_no_auth:
            return "dev"
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        try:
            return brain.pairing.authorize(authorization[7:])
        except AuthError as e:
            raise HTTPException(401, str(e))

    def hook_auth(x_brain_hook_token: str = Header(default="")) -> None:
        if x_brain_hook_token != brain.hook_token:
            raise HTTPException(403, "bad hook token")

    def idem(request: Request, key: str | None, device: str, body: Any, fn):
        if not key:
            return fn()
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        row = brain.db.one("SELECT * FROM idempotency WHERE key=?", (key,))
        if row:
            if row["request_sha256"] != digest:
                raise HTTPException(422, "idempotency key reused with a different body")
            return json.loads(row["response"])
        result = fn()
        brain.db.x("INSERT INTO idempotency(key, device_id, request_sha256, response, created_at) VALUES (?,?,?,?,?)",
                   (key, device, digest, json.dumps(result, default=str), time.time()))
        return result

    async def aidem(request: Request, key: str | None, device: str, body: Any, coro_fn):
        if not key:
            return await coro_fn()
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        row = brain.db.one("SELECT * FROM idempotency WHERE key=?", (key,))
        if row:
            if row["request_sha256"] != digest:
                raise HTTPException(422, "idempotency key reused with a different body")
            return json.loads(row["response"])
        result = await coro_fn()
        brain.db.x("INSERT INTO idempotency(key, device_id, request_sha256, response, created_at) VALUES (?,?,?,?,?)",
                   (key, device, digest, json.dumps(result, default=str), time.time()))
        return result

    # ---- unauthenticated -------------------------------------------------------------------------
    @app.get("/v1/health")
    def health() -> dict:
        prof = {}
        if brain.p.site_profile.exists():
            import yaml
            prof = yaml.safe_load(brain.p.site_profile.read_text()) or {}
        return {"ok": True, "brain_pub": brain.ident.pub_b64, "version": __version__,
                "rung": prof.get("rung", "unknown"), "paused": brain.paused}

    @app.post("/v1/pair/complete")
    def pair_complete(b: PairComplete) -> dict:
        try:
            return brain.pairing.complete(b.code, b.device_pub, b.device_name, b.nonce_device, b.sig, b.mac)
        except AuthError as e:
            raise HTTPException(403, str(e))

    @app.post("/v1/session/challenge")
    def challenge(b: Challenge, request: Request) -> dict:
        host = request.headers.get("host", "")
        try:
            return brain.pairing.challenge(b.device_id, host_of(f"https://{host}") if host else "")
        except AuthError as e:
            raise HTTPException(401, str(e))

    @app.post("/v1/session/token")
    def token(b: TokenReq) -> dict:
        try:
            return brain.pairing.token(b.device_id, b.nonce, b.sig)
        except AuthError as e:
            raise HTTPException(401, str(e))

    @app.post("/v1/session/refresh")
    def refresh(b: RefreshReq) -> dict:
        try:
            return brain.pairing.refresh(b.device_id, b.refresh_token)
        except AuthError as e:
            raise HTTPException(401, str(e))

    # ---- authenticated ---------------------------------------------------------------------------
    @app.get("/v1/repos")
    def list_repos(device: str = Depends(auth)) -> list[dict]:
        return [{"id": r.id, "name": r.name or r.id, "path": r.path, "default_branch": r.default_branch,
                 "profile": r.profile, "archive_policy": r.archive_policy} for r in brain.settings.repos]

    @app.get("/v1/threads")
    def list_threads(repo_id: str | None = None, state: str | None = None, archived: int = 0,
                     device: str = Depends(auth)) -> list[dict]:
        return brain.list_threads(repo_id, state, bool(archived))

    @app.post("/v1/threads")
    def new_thread(b: NewThread, request: Request, device: str = Depends(auth),
                   idempotency_key: str | None = Header(default=None)) -> dict:
        def go():
            try:
                return brain.create_thread(b.repo_id, b.title, b.mode, b.provider, b.base_ref)
            except KeyError as e:
                raise HTTPException(404, str(e))
        return idem(request, idempotency_key, device, b.model_dump(), go)

    @app.get("/v1/threads/{tid}")
    def get_thread(tid: str, device: str = Depends(auth)) -> dict:
        t = brain.thread(tid)
        if not t:
            raise HTTPException(404, "no such thread")
        return brain.thread_view(t)

    @app.patch("/v1/threads/{tid}")
    def patch_thread(tid: str, b: PatchThread, device: str = Depends(auth)) -> dict:
        if not brain.thread(tid):
            raise HTTPException(404, "no such thread")
        return brain.patch_thread(tid, **b.model_dump())

    @app.post("/v1/threads/{tid}/fork")
    def fork(tid: str, b: ForkReq, request: Request, device: str = Depends(auth),
             idempotency_key: str | None = Header(default=None)) -> dict:
        def go():
            try:
                return brain.fork_thread(tid, b.title, b.mode, b.at_seq)
            except KeyError as e:
                raise HTTPException(404, str(e))
        return idem(request, idempotency_key, device, b.model_dump() | {"tid": tid}, go)

    @app.post("/v1/threads/{tid}/messages")
    async def message(tid: str, b: MessageReq, request: Request, device: str = Depends(auth),
                      idempotency_key: str | None = Header(default=None)) -> dict:
        async def go():
            try:
                seq = await brain.send_message(tid, b.text, {"kind": "user", "device_id": device})
            except KeyError:
                raise HTTPException(404, "no such thread")
            return {"seq": seq}
        return await aidem(request, idempotency_key, device, b.model_dump() | {"tid": tid}, go)

    @app.post("/v1/threads/{tid}/stop")
    async def stop(tid: str, device: str = Depends(auth)) -> dict:
        await brain.stop(tid)
        return {"ok": True}

    @app.get("/v1/threads/{tid}/events")
    def events(tid: str, after_seq: int = 0, limit: int = 200, device: str = Depends(auth)) -> dict:
        evs = brain.events.replay(tid, after_seq, limit)
        return {"events": evs, "last_seq": brain.events.last_seq(tid)}

    @app.get("/v1/search")
    def search(q: str, device: str = Depends(auth)) -> list[dict]:
        return brain.events.search(q)

    @app.get("/v1/pending")
    def pending(device: str = Depends(auth)) -> dict:
        return brain.pending()

    @app.get("/v1/approvals/{aid}")
    def get_approval(aid: str, device: str = Depends(auth)) -> dict:
        a = brain.approval(aid)
        if not a:
            raise HTTPException(404, "no such approval")
        return a

    @app.post("/v1/approvals/{aid}/decision")
    async def decide(aid: str, b: DecisionReq, device: str = Depends(auth)) -> dict:
        if b.decision not in ("allow", "deny"):
            raise HTTPException(422, "decision must be allow or deny")
        if not dev_no_auth and not brain.pairing.verify_decision(device, aid, b.decision, b.device_sig):
            raise HTTPException(403, "decision signature does not verify")
        try:
            return await brain.decide(aid, b.decision, b.note, device)
        except KeyError:
            raise HTTPException(404, "no such approval")

    @app.get("/v1/jobs")
    def jobs(device: str = Depends(auth)) -> list[dict]:
        return brain.jobs()

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel(job_id: str, device: str = Depends(auth)) -> dict:
        if not brain.db.one("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)):
            raise HTTPException(404, "not a Brain job")
        return {"ok": brain.cancel_job(job_id)}

    @app.post("/v1/pause")
    async def pause(device: str = Depends(auth)) -> dict:
        return {"paused": await brain.set_paused(True)}

    @app.post("/v1/resume")
    async def resume(device: str = Depends(auth)) -> dict:
        return {"paused": await brain.set_paused(False)}

    @app.get("/v1/ledger")
    def ledger(device: str = Depends(auth)) -> dict:
        return brain.ledger_today()

    @app.post("/v1/devices/push-token")
    def push_token(b: PushToken, device: str = Depends(auth)) -> dict:
        brain.db.x("UPDATE devices SET fcm_token=? WHERE id=?", (b.fcm_token, device))
        return {"ok": True}

    @app.get("/v1/blobs/{bid}")
    def blob(bid: str, device: str = Depends(auth)):
        p = brain.events.blob_path(bid)
        if not p:
            raise HTTPException(404, "no such blob")
        return FileResponse(p)

    # ---- internal (Unix socket only; hook token required) ----------------------------------------
    @app.post("/internal/hook")
    async def hook(b: HookReq, _: None = Depends(hook_auth)) -> dict:
        return await brain.pretool(b.thread_id, b.tool_name or "", b.tool_input, b.tool_use_id)

    @app.post("/internal/submit")
    def internal_submit(b: SubmitReq, _: None = Depends(hook_auth)) -> dict:
        from pathlib import Path
        p = Path(b.script)
        if not p.is_file():
            raise HTTPException(404, f"script not found: {b.script}")
        try:
            return brain.submit_job(b.thread_id, p, b.sbatch_args)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/internal/pair")
    def internal_pair(body: dict, _: None = Depends(hook_auth)) -> dict:
        endpoint = body.get("endpoint") or brain.settings.endpoint
        if not endpoint:
            raise HTTPException(400, "no endpoint configured; pass one")
        return brain.pairing.create(endpoint) | {"fingerprint": brain.ident.fingerprint()}

    @app.post("/internal/endpoint")
    def internal_endpoint(body: dict, _: None = Depends(hook_auth)) -> dict:
        return {"pushed_to": brain.set_endpoint(body["endpoint"])}

    @app.post("/internal/devices")
    def internal_devices(body: dict, _: None = Depends(hook_auth)) -> list[dict]:
        if body.get("revoke"):
            brain.pairing.revoke(body["revoke"])
        return [{"id": r["id"], "name": r["name"], "last_seen": r["last_seen"], "revoked": bool(r["revoked"])}
                for r in brain.db.q("SELECT * FROM devices ORDER BY created_at")]

    # ---- WebSocket -------------------------------------------------------------------------------
    @app.websocket("/v1/ws")
    async def ws(sock: WebSocket, token: str = "") -> None:
        if not dev_no_auth:
            try:
                brain.pairing.authorize(token)
            except AuthError:
                await sock.close(code=4401)
                return
        await sock.accept()
        subs: dict[str, asyncio.Queue] = {}
        pumps: dict[str, asyncio.Task] = {}

        async def pump(thread_id: str, q: asyncio.Queue, after: int) -> None:
            last = after
            for ev in brain.events.replay(thread_id, after, 1000):
                await sock.send_json({"op": "event", "event": ev})
                last = ev["seq"]
            await sock.send_json({"op": "replay_complete", "thread_id": thread_id, "through_seq": last})
            while True:
                ev = await q.get()
                if ev["seq"] > last:
                    await sock.send_json({"op": "event", "event": ev})
                    last = ev["seq"]

        try:
            while True:
                msg = await sock.receive_json()
                op = msg.get("op")
                if op == "ping":
                    await sock.send_json({"op": "pong", "ts": time.time()})
                elif op == "subscribe":
                    tid = msg.get("thread_id", "")
                    if tid in subs:
                        continue
                    q = brain.events.subscribe(tid)
                    subs[tid] = q
                    pumps[tid] = asyncio.create_task(pump(tid, q, int(msg.get("after_seq", 0))))
                elif op == "unsubscribe":
                    tid = msg.get("thread_id", "")
                    if tid in pumps:
                        pumps.pop(tid).cancel()
                        brain.events.unsubscribe(tid, subs.pop(tid))
        except WebSocketDisconnect:
            pass
        finally:
            for tid, task in pumps.items():
                task.cancel()
                brain.events.unsubscribe(tid, subs[tid])

    return app
