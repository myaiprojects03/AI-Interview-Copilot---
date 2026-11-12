"""Live interview session WebSocket: authentication and message transport only.
The session logic lives in app/copilot/session.py.

Connect with ?ticket=<ticket> from POST /api/session/ticket (single use, valid 30 s; see app/auth.py).
An invalid, used or expired ticket gets {"type": "error", "code": "auth", "message": "..."} and
close code 4401.

Protocol (client -> server):
  text  {"type": "start", "session_type": "behavioral|technical|mixed", "sample_rate": 16000,
         "cv_id": "<uuid or null>", "jd_id": "<uuid or null>",   (no cv_id = sample CV)
         "resume": "<session_id>"}   optional: after a dropped connection, continue that interview
                                     (same history record) if it ended less than 2 minutes ago
  bytes first byte = channel (0 = interviewer/tab, 1 = candidate/mic), rest = PCM16 LE mono
  text  {"type": "regenerate"}   re-answer the current question
  text  {"type": "stop"}         stop the answer being generated
  text  {"type": "end"}          end the session

Protocol (server -> client):
  {"type": "ready", "channels": [...], "answers": bool, "cv": "<title>", "jd": "<title or null>",
   "session_id": "<id or null>", "resumed": bool, "remaining_seconds": <int or null = unlimited>}
  {"type": "transcript", "speaker": "...", "text": "...", "is_final": bool, "end_of_turn": bool}
  {"type": "question", "id": "...", "text": "...", "category": "behavioral|technical|other",
   "replaces": "<id>"}   (replaces: optional, a narrowed follow-up supersedes that question)
  {"type": "answer_start", "question_id": "..."}
  {"type": "token", "question_id": "...", "text": "..."}
  {"type": "answer_done", "question_id": "...", "stopped": bool, "timings": {...},
   "unverified": ["30%", ...]}   (numbers in the answer that are not in the CV)
  {"type": "usage", "remaining_seconds": <int or null>}     every 15 s
  {"type": "info" | "warning" | "error", "message": "...", "code": "auth" | "limit" (optional)}
     e.g. warning "Transcription of the interviewer was interrupted. Reconnecting..." then info
     "... reconnected." (the server reopens a dropped transcription stream up to 3 times)
  Close codes: 4401 not signed in, 4402 plan limit reached.

Timings (ms, measured on the server from the moment the interviewer's final transcript arrives):
  detect_ms       question detection finished
  retrieval_ms    CV/JD retrieval time (runs in parallel with detection)
  first_token_ms  first answer token sent  <- the user-perceived latency
  total_ms        answer complete
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.auth import TICKET_TTL_S, current_user_id, issue_ticket, websocket_user_id
from app.copilot.session import CopilotSession, Services
from app.users.security import TokenError

log = logging.getLogger(__name__)
router = APIRouter()

CLOSE_CODES = {"limit": 4402}


@router.post("/api/session/ticket")
async def session_ticket(request: Request, user_id: str = Depends(current_user_id)) -> dict:
    try:
        ticket = await issue_ticket(request.app, user_id)
    except Exception as exc:
        log.exception("could not issue a session ticket")
        raise HTTPException(503, "Live sessions are unavailable right now. Try again in a minute.") from exc
    return {"ticket": ticket, "expires_in": TICKET_TTL_S}


class Outbox:
    """Single writer: every outbound message goes through one task, so sends never interleave."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self.task = asyncio.create_task(self._run())

    async def send(self, payload: dict) -> None:
        await self.queue.put(payload)

    async def _run(self) -> None:
        while True:
            payload = await self.queue.get()
            if payload is None:
                return
            try:
                await self.ws.send_json(payload)
            except Exception:
                return

    async def close(self) -> None:
        await self.queue.put(None)
        await asyncio.wait([self.task], timeout=2)


@router.websocket("/ws/session")
async def session_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        user_id = await websocket_user_id(ws)
    except TokenError as exc:
        # 4401: application close code for "not authenticated" (the frontend sends the user to sign in).
        await ws.send_json({"type": "error", "code": "auth", "message": str(exc)})
        await ws.close(code=4401)
        return

    outbox = Outbox(ws)
    ended = asyncio.Event()
    end_reason: list[str] = []

    async def request_end(reason: str) -> None:
        end_reason.append(reason)
        ended.set()

    session = CopilotSession(outbox.send, Services.from_state(ws.app.state), user_id, request_end)
    receiving: asyncio.Future | None = None
    client_gone = False
    try:
        while True:
            receiving = asyncio.ensure_future(ws.receive())
            stop = asyncio.ensure_future(ended.wait())
            done, _ = await asyncio.wait({receiving, stop}, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            if receiving not in done:
                break  # the session asked to end (plan limit reached)
            msg = receiving.result()
            if msg.get("type") == "websocket.disconnect":
                client_gone = True
                break
            if msg.get("bytes") is not None:
                await session.audio(msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    event = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "start":
                    await session.start(
                        int(event.get("sample_rate", 16000)),
                        cv_id=event.get("cv_id") or None,
                        jd_id=event.get("jd_id") or None,
                        session_type=str(event.get("session_type") or "mixed"),
                        resume=str(event["resume"]) if event.get("resume") else None,
                    )
                elif kind == "regenerate":
                    session.regenerate()
                elif kind == "stop":
                    session.stop_answer()
                elif kind == "end":
                    break
    except WebSocketDisconnect:
        client_gone = True
    finally:
        if receiving is not None and not receiving.done():
            receiving.cancel()
        await session.close()
        await outbox.close()
        # Always close explicitly, so the browser knows the session is over (and why).
        if not client_gone:
            try:
                await ws.close(code=CLOSE_CODES.get(end_reason[0], 1000) if end_reason else 1000)
            except Exception:
                pass
        log.info("session closed%s", f" ({end_reason[0]})" if end_reason else "")
