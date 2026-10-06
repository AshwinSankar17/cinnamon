"""HTTP + WebSocket API (protocol/API.md)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__
from .core import Brain
from .identity import AuthError, PendingApproval, host_of


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
    run: bool = False              # a training run: worktree + its own vault folder
    message: str | None = None     # optional first message, sent right after creation


class NewProject(BaseModel):
    name: str
    idea: str
    provider: str | None = None


class PatchThread(BaseModel):
    title: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    muted: bool | None = None


class ForkReq(BaseModel):
    title: str
    mode: str = "worktree"
    at_seq: int | None = None


MAX_UPLOAD = 25 * 1024 * 1024


class ListenReq(BaseModel):
    thread_id: str
    path: str


class RatingsReq(BaseModel):
    ratings: dict[str, dict[str, Any]]


class SwitchReq(BaseModel):
    provider: str


class MessageReq(BaseModel):
    text: str


class DecisionReq(BaseModel):
    decision: str
    note: str | None = None
    device_sig: str


class PushToken(BaseModel):
    fcm_token: str


class WebPushSub(BaseModel):
    subscription: dict[str, Any]


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
        if not hmac.compare_digest(x_brain_hook_token, brain.hook_token):
            raise HTTPException(403, "bad hook token")

    def admin_auth(x_brain_admin_token: str = Header(default="")) -> None:
        """Pairing, devices, endpoints: the owner's CLI only. Agents hold the hook token, which is refused here."""
        if not hmac.compare_digest(x_brain_admin_token, brain.admin_token):
            raise HTTPException(403, "admin token required (this is not available to agents)")

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
            out = brain.pairing.complete(b.code, b.device_pub, b.device_name, b.nonce_device, b.sig, b.mac)
        except AuthError as e:
            raise HTTPException(403, str(e))
        if out.get("pending_approval"):
            brain.request_device_approval(out["device_id"], b.device_name)
        return out

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
        except PendingApproval as e:
            raise HTTPException(403, {"error": "pending_approval", "message": str(e)})
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
        return [brain.repo_view(r) for r in brain.settings.repos]

    @app.get("/v1/endpoints")
    def endpoints(device: str = Depends(auth)) -> dict:
        return {"endpoints": brain.endpoints}

    @app.post("/v1/projects")
    async def new_project(b: NewProject, request: Request, device: str = Depends(auth),
                          idempotency_key: str | None = Header(default=None)) -> dict:
        async def go():
            try:
                out = brain.create_project(b.name, b.idea, b.provider)
            except (ValueError, KeyError) as e:
                raise HTTPException(422, str(e))
            except FileExistsError as e:
                raise HTTPException(409, str(e))
            await brain.send_message(out["thread"]["id"], f"New project: {b.name}\n\n{b.idea}",
                                     {"kind": "user", "device_id": device})
            return out
        return await aidem(request, idempotency_key, device, b.model_dump(), go)

    @app.get("/v1/threads")
    def list_threads(repo_id: str | None = None, state: str | None = None, archived: int = 0,
                     device: str = Depends(auth)) -> list[dict]:
        return brain.list_threads(repo_id, state, bool(archived))

    @app.post("/v1/threads")
    async def new_thread(b: NewThread, request: Request, device: str = Depends(auth),
                         idempotency_key: str | None = Header(default=None)) -> dict:
        async def go():
            try:
                t = brain.create_thread(b.repo_id, b.title, b.mode, b.provider, b.base_ref, run=b.run)
            except KeyError as e:
                raise HTTPException(404, str(e))
            except (ValueError, FileExistsError) as e:
                raise HTTPException(422, str(e))
            if b.message and b.message.strip():
                await brain.send_message(t["id"], b.message, {"kind": "user", "device_id": device})
            return t
        return await aidem(request, idempotency_key, device, b.model_dump(), go)

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

    @app.post("/v1/threads/{tid}/attachments")
    async def attach(tid: str, request: Request, device: str = Depends(auth),
                     x_filename: str = Header(default="file")) -> dict:
        """Raw-body upload (no multipart dependency). Stored in the thread's own worktree, git-excluded."""
        if not brain.thread(tid):
            raise HTTPException(404, "no such thread")
        data, limit = bytearray(), MAX_UPLOAD
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                raise HTTPException(413, f"attachments are limited to {limit // (1024 * 1024)} MB")
        try:
            return brain.save_attachment(tid, unquote(x_filename), bytes(data), request.headers.get("content-type", ""))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/v1/listening")
    def listening_list(device: str = Depends(auth)) -> list[dict]:
        return [dict(r) for r in brain.db.q("SELECT id, thread_id, title, n_items, n_systems, state, summary, created_at,"
                                            " rated_at FROM listening_tests ORDER BY created_at DESC LIMIT 200")]

    @app.get("/v1/listening/{lid}")
    def listening_get(lid: str, device: str = Depends(auth)) -> dict:
        from .listening import ListenError
        try:
            return brain.listening(lid)
        except KeyError:
            raise HTTPException(404, "no such listening test")
        except ListenError as e:
            raise HTTPException(410, f"the test's files changed: {e}")

    @app.get("/v1/listening/{lid}/audio")
    def listening_audio(lid: str, path: str, device: str = Depends(auth)):
        from .listening import MEDIA, ListenError
        try:
            f = brain.listening_file(lid, path)
        except (KeyError, ListenError):
            raise HTTPException(404, "no such audio file")
        return FileResponse(f, media_type=MEDIA.get(f.suffix.lower()), headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/v1/listening/{lid}/ratings")
    async def listening_rate(lid: str, b: RatingsReq, device: str = Depends(auth)) -> dict:
        try:
            return await brain.rate_listening(lid, b.ratings, device)
        except KeyError:
            raise HTTPException(404, "no such listening test")

    @app.get("/v1/threads/{tid}/files")
    def thread_files(tid: str, root: str = "worktree", path: str = "", device: str = Depends(auth)) -> dict:
        t = brain.thread(tid)
        if not t:
            raise HTTPException(404, "no such thread")
        try:
            return brain.list_files(t, root, path)
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/v1/threads/{tid}/file")
    def thread_file(tid: str, root: str, path: str, device: str = Depends(auth)):
        from .listening import MEDIA, ListenError, resolve_file
        t = brain.thread(tid)
        if not t:
            raise HTTPException(404, "no such thread")
        try:
            listing = brain.list_files(t, root, "")
            f = resolve_file(Path(listing["abs"]), path, brain.scope_roots(t))
        except (KeyError, ListenError):
            raise HTTPException(404, "no such file")
        if f.resolve().is_relative_to(brain.p.root.resolve()):
            raise HTTPException(403, "Brain's own files are not served")
        media = MEDIA.get(f.suffix.lower()) or ("text/plain; charset=utf-8" if f.stat().st_size < 5_000_000 else None)
        return FileResponse(f, media_type=media, headers={"Cache-Control": "private, no-cache"})

    @app.get("/v1/threads/{tid}/skills")
    def thread_skills(tid: str, device: str = Depends(auth)) -> list[dict]:
        """Skills and slash commands the thread's current provider can run as "/name args"."""
        from .providers.agy import app_dir
        from .skills import list_skills
        t = brain.thread(tid)
        if not t:
            raise HTTPException(404, "no such thread")
        ctx = brain.provider_ctx(t)
        return list_skills(t["provider"], ctx.cwd, ctx.env, app_dir(ctx)[1] if t["provider"] == "agy" else None)

    @app.get("/v1/providers")
    def providers(device: str = Depends(auth)) -> list[dict]:
        return [{"name": n, "native_handoff": getattr(p, "hydrate", None) is not None, "defer": getattr(p, "supports_defer", True)}
                for n, p in brain.providers.items()]

    @app.post("/v1/threads/{tid}/provider")
    def switch_provider(tid: str, b: SwitchReq, device: str = Depends(auth)) -> dict:
        try:
            return brain.switch_provider(tid, b.provider)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))

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

    @app.get("/v1/push/vapid")
    def vapid(device: str = Depends(auth)) -> dict:
        return {"public_key": brain.webpush.public_key}

    @app.post("/v1/devices/web-push")
    def web_push(b: WebPushSub, device: str = Depends(auth)) -> dict:
        sub = b.subscription
        ep, keys = sub.get("endpoint"), sub.get("keys") or {}
        if not (isinstance(ep, str) and ep.startswith("https://") and keys.get("p256dh") and keys.get("auth")):
            raise HTTPException(422, "subscription needs an https endpoint and p256dh/auth keys")
        brain.db.x("UPDATE devices SET web_push=? WHERE id=?",
                   (json.dumps({"endpoint": ep, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}), device))
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

    @app.post("/internal/listen")
    def internal_listen(b: ListenReq, _: None = Depends(hook_auth)) -> dict:
        from .listening import ListenError
        try:
            return brain.register_listening(b.thread_id, b.path)
        except ListenError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except KeyError:
            raise HTTPException(404, "no such thread")

    @app.post("/internal/pair")
    def internal_pair(body: dict, _: None = Depends(admin_auth)) -> dict:
        endpoint = body.get("endpoint") or brain.settings.endpoint
        if not endpoint:
            raise HTTPException(400, "no endpoint configured; pass one")
        return brain.pairing.create(endpoint, [e for e in brain.endpoints if e != endpoint]) | {
            "fingerprint": brain.ident.fingerprint()}

    @app.post("/internal/endpoint")
    def internal_endpoint(body: dict, _: None = Depends(admin_auth)) -> dict:
        return {"pushed_to": brain.set_endpoint(body["endpoint"], body.get("alternates"))}

    @app.post("/internal/devices")
    def internal_devices(body: dict, _: None = Depends(admin_auth)) -> list[dict]:
        if body.get("revoke"):
            brain.pairing.revoke(body["revoke"])
        if body.get("approve"):
            brain.approve_device(body["approve"])
        return [{"id": r["id"], "name": r["name"], "last_seen": r["last_seen"], "revoked": bool(r["revoked"]),
                 "approved": bool(r["approved"])} for r in brain.db.q("SELECT * FROM devices ORDER BY created_at")]

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

    _mount_pwa(app, brain)
    return app


def pwa_dir(brain: Brain) -> Path | None:
    """The exported PWA (`npx expo export --platform web --output-dir dist` in mobile/)."""
    d = brain.settings.raw.get("web_app_dir") or str(Path(__file__).resolve().parents[2] / "mobile" / "dist")
    p = Path(d).expanduser()
    return p if (p / "index.html").is_file() else None


def _mount_pwa(app: FastAPI, brain: Brain) -> None:
    """Serve the PWA on the same origin as the API: static files, index.html for app routes (/thread/x...).
    Registered last, so every /v1 and /internal route wins. Unauthenticated, like any web page: it holds no data."""
    headers = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
               "Permissions-Policy": "camera=(self), microphone=(), geolocation=()"}

    @app.get("/{path:path}", include_in_schema=False)
    def pwa(path: str):
        root = pwa_dir(brain)
        if path.startswith(("v1/", "internal/")) or not root:
            raise HTTPException(404, "not found")
        f = (root / path).resolve()
        if path and f.is_file() and f.is_relative_to(root.resolve()):
            # content-hashed bundles cache forever; the service worker and shell must always revalidate
            immutable = path.startswith("_expo/static/") or path.startswith("assets/")
            h = {**headers, "Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"}
            if path == "sw.js":
                h["Service-Worker-Allowed"] = "/"
            media = "application/manifest+json" if f.suffix == ".webmanifest" else None
            return FileResponse(f, headers=h, media_type=media)
        return FileResponse(root / "index.html", headers={**headers, "Cache-Control": "no-cache"})
