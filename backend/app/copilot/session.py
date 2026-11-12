"""A live copilot session: transcription -> question detection -> retrieval -> streamed answer,
plus interview history and plan usage limits.

Transport-agnostic: it receives audio and commands, and emits messages through `send`. The WebSocket
handler (app/api/session_ws.py) owns the connection. Message formats are documented there.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.billing.plans import limit_seconds, plan_for
from app.config import get_settings
from app.copilot.detector import detect_question, has_question_cue, is_duplicate
from app.copilot.factcheck import unverified_numbers
from app.copilot.prompts import answer_system_for, format_history
from app.copilot.recorder import SessionRecorder
from app.documents.repo import Document, DocumentRepo
from app.history.repo import HistoryRepo
from app.providers.base import ChatMessage, LLMError, LLMProvider, Speaker, STTProvider, STTStream
from app.providers.registry import get_llm, get_stt
from app.retrieval.service import RetrievalService
from app.users.repo import UserRepo

log = logging.getLogger(__name__)

CHANNELS: dict[int, Speaker] = {0: "interviewer", 1: "candidate"}
SAMPLE_CV = Path(__file__).resolve().parents[1] / "sample_data" / "sample_cv.md"
HISTORY_LINES = 12              # transcript lines given to the models as context
DETECT_TIMEOUT_S = 6
PARTIAL_CHECK_INTERVAL_S = 2.5  # at most one mid-turn check per 2.5 s (Groq free tier: 30 req/min)
PARTIAL_MIN_NEW_CHARS = 25      # about 5 new words since the last check
RETRIEVE_WAIT_S = 1.0           # max extra wait for retrieval after a question is detected
RETRIEVE_TOP_K = 4
REPLACE_WINDOW_S = 10           # a narrowed follow-up in the same turn replaces the previous question
METER_INTERVAL_S = 15           # how often usage is recorded and the plan limit checked
LIMIT_WARNING_S = 120           # warn when this much time is left on the plan
REGENERATE_MIN_INTERVAL_S = 1.0 # ignore repeated Regenerate presses faster than this (protects the LLM quota)
ROTATE_QUIET_S = 2.5            # "quiet": nobody has spoken for this long and no answer is being written
RESUME_WINDOW_S = 120           # a dropped connection can resume the same interview within this time
STT_RETRY_DELAYS_S = (1, 2, 4)  # reconnect attempts after the transcription service drops
STT_STABLE_S = 30               # a stream that ran this long resets the attempt count
SPEAKER_NAMES = {"interviewer": "the interviewer", "candidate": "you"}

Send = Callable[[dict], Awaitable[None]]


@lru_cache
def load_sample_cv() -> str:
    return SAMPLE_CV.read_text(encoding="utf-8")


@dataclass
class Question:
    id: str
    text: str
    category: str
    turn: int = 0                      # interviewer turn it was detected in
    detected_at: float = 0.0
    highlights: list[str] = field(default_factory=list)   # retrieved CV/JD passages
    retrieval_ms: int | None = None


def public_error(exc: BaseException, fallback: str) -> str:
    """What the person in the interview sees. In development: the real reason (e.g. which key is
    missing in .env). Elsewhere: a plain message; the details are in the server log."""
    if get_settings().app_env == "development":
        return str(exc)
    if isinstance(exc, LLMError) and "rate limit" in str(exc).lower():
        return "The answer service is busy. Press Regenerate in a few seconds."
    return fallback


def ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


@dataclass
class Services:
    """What a session needs from the application. Any of them may be missing (e.g. the database
    is down); the session then degrades gracefully instead of failing."""
    documents: DocumentRepo | None = None
    users: UserRepo | None = None
    history: HistoryRepo | None = None
    retrieval: RetrievalService | None = None

    @classmethod
    def from_state(cls, state: Any) -> Services:
        return cls(getattr(state, "repo", None), getattr(state, "users", None),
                   getattr(state, "history", None), getattr(state, "retrieval", None))


class CopilotSession:
    def __init__(self, send: Send, services: Services, user_id: str,
                 request_end: Callable[[str], Awaitable[None]] | None = None):
        self.send = send
        self.services = services
        self.user_id = user_id
        self._request_end = request_end
        self.started = False
        self.closing = False
        self.connection_id = uuid.uuid4().hex
        self.stt: STTProvider | None = None
        self.sample_rate = 16000
        self.streams: dict[Speaker, STTStream] = {}
        self.tasks: list[asyncio.Task] = []

        self.llm: LLMProvider | None = None
        self.history: deque[tuple[str, str]] = deque(maxlen=HISTORY_LINES)
        self.turns: asyncio.Queue[tuple[str, float, list[tuple[str, str]], int]] = asyncio.Queue()
        self.turn_seq = 0  # incremented when an interviewer turn ends
        self.asked: list[str] = []
        self.partial_checked_len = 0
        self.last_partial_check = 0.0
        self.current: Question | None = None
        self.answer_task: asyncio.Task | None = None
        self.detecting = False
        self.last_speech = time.monotonic()
        self.rotate_task: asyncio.Task | None = None
        self.last_regenerate = 0.0
        self.detector_task: asyncio.Task | None = None

        self.cv_text = load_sample_cv()
        self.jd_text: str | None = None
        self.session_type = "mixed"
        self.doc_ids: list[str] = []

        self.recorder: SessionRecorder | None = None
        self.session_id: str | None = None
        self.limit_s: int | None = None
        self.used_before_s = 0
        self.session_s = 0.0                      # time used in this session (exact)
        self.recorded_s = 0                       # whole seconds already saved to history
        self.metered_since: float | None = None   # monotonic time the meter started
        self.meter_task: asyncio.Task | None = None

    # ---------- transcription ----------
    async def _on_event(self, ev) -> None:
        self.last_speech = time.monotonic()
        await self.send({"type": "transcript", "speaker": ev.speaker, "text": ev.text,
                         "is_final": ev.is_final, "end_of_turn": ev.speech_final})
        if ev.is_final:
            if self.recorder:
                self.recorder.line(ev.speaker, ev.text)
            # Snapshot the context before this turn, so detection is not affected by
            # lines that arrive while it runs.
            context = list(self.history)
            self.history.append((ev.speaker, ev.text))
            if ev.speaker == "interviewer" and self.llm is not None:
                self.partial_checked_len = 0
                await self.turns.put((ev.text, time.perf_counter(), context, self.turn_seq))
                self.turn_seq += 1
        elif ev.speaker == "interviewer" and self.llm is not None:
            await self._maybe_check_partial(ev.text)

    async def _transcribe(self, speaker: Speaker, stream: STTStream | None) -> None:
        """Forward one speaker's transcript. If the transcription service drops the stream (network
        blip, provider restart), reopen it: a few seconds of missed words beat a dead session."""
        failures = 0
        who = SPEAKER_NAMES.get(speaker, speaker)
        while True:
            opened_at = time.monotonic()
            if stream is not None:
                self.streams[speaker] = stream
                try:
                    async for ev in stream.events():
                        await self._on_event(ev)
                except Exception as exc:
                    if not self.closing:
                        log.warning("STT stream for %s failed: %s", speaker, exc)
                finally:
                    if self.streams.get(speaker) is stream:
                        self.streams.pop(speaker, None)
            if self.closing:
                return
            if stream is not None and time.monotonic() - opened_at > STT_STABLE_S:
                failures = 0
            failures += 1
            if failures > len(STT_RETRY_DELAYS_S) or self.stt is None:
                await self.send({"type": "error", "message": f"Transcription of {who} stopped and could not be "
                                                             f"restarted. End the interview and start again."})
                return
            await self.send({"type": "warning", "message": f"Transcription of {who} was interrupted. Reconnecting..."})
            await asyncio.sleep(STT_RETRY_DELAYS_S[failures - 1])
            if self.closing:
                return
            try:
                stream = await self.stt.open_stream(speaker, sample_rate=self.sample_rate)
            except Exception as exc:
                log.warning("could not reopen STT stream for %s: %s", speaker, exc)
                stream = None
                continue
            log.info("STT stream for %s reconnected", speaker)
            await self.send({"type": "info", "message": f"Transcription of {who} reconnected."})

    # ---------- question detection ----------
    async def _maybe_check_partial(self, text: str) -> None:
        """Detect questions inside a turn that is still in progress.

        A turn only ends after silence. If the interviewer keeps talking, or the candidate answers
        on the same audio source, the question would otherwise wait for the turn to end. Checks are
        throttled and only run when the new words contain a question cue, to protect latency and
        the free-tier rate limit.
        """
        if len(text) < self.partial_checked_len:  # provider revised the partial: rescan
            self.partial_checked_len = 0
        new_words = text[self.partial_checked_len:]
        now = time.perf_counter()
        if (len(new_words) < PARTIAL_MIN_NEW_CHARS
                or now - self.last_partial_check < PARTIAL_CHECK_INTERVAL_S
                or not has_question_cue(new_words)):
            return
        self.partial_checked_len = len(text)
        self.last_partial_check = now
        await self.turns.put((text, now, list(self.history), self.turn_seq))

    async def _retrieve(self, text: str) -> tuple[list[str], int]:
        t = time.perf_counter()
        assert self.services.retrieval is not None
        chunks = await self.services.retrieval.retrieve(self.user_id, text, self.doc_ids, top_k=RETRIEVE_TOP_K)
        return [c.text for c in chunks if c.text], ms(t)

    async def _detector(self) -> None:
        assert self.llm is not None
        while True:
            self.detecting = False
            text, t0, context, turn = await self.turns.get()
            self.detecting = True
            # Retrieval runs at the same time as detection, on the turn text, so it adds no
            # latency in the common case.
            retrieving = (asyncio.create_task(self._retrieve(text))
                          if self.services.retrieval is not None and self.doc_ids and has_question_cue(text)
                          else None)
            try:
                found = await asyncio.wait_for(
                    detect_question(self.llm, context, text, self.asked), timeout=DETECT_TIMEOUT_S)
            except TimeoutError:
                log.warning("question detection timed out")
                found = None
            except LLMError as exc:
                log.warning("question detection failed: %s", exc)
                await self.send({"type": "warning", "message": public_error(
                    exc, "Question detection is having trouble, so answers may be delayed.")})
                found = None
            except Exception:
                log.exception("question detection failed")
                found = None
            if found is None or is_duplicate(found.question, self.asked):
                if retrieving:
                    retrieving.cancel()
                continue
            self.asked.append(found.question)
            q = Question(uuid.uuid4().hex[:8], found.question, found.category, turn=turn,
                         detected_at=time.perf_counter())
            detect_ms = ms(t0)
            if retrieving:
                try:
                    q.highlights, q.retrieval_ms = await asyncio.wait_for(retrieving, timeout=RETRIEVE_WAIT_S)
                except TimeoutError:
                    log.warning("retrieval too slow; answering without retrieved passages")
                except Exception as exc:
                    log.warning("retrieval failed (%s); answering without retrieved passages", exc)
            log.info("question %s (%s) detected in %d ms, %d passages: %s", q.id, q.category, detect_ms,
                     len(q.highlights), q.text)
            # A narrowed follow-up asked in the same breath replaces the broader question
            # ("dive into persuasion? ... tell me about a time you convinced someone").
            previous = self.current
            replaces = (previous.id if previous and previous.turn == turn
                        and q.detected_at - previous.detected_at < REPLACE_WINDOW_S else None)
            self.current = q
            msg = {"type": "question", "id": q.id, "text": q.text, "category": q.category}
            if replaces:
                msg["replaces"] = replaces
                if self.recorder:
                    self.recorder.forget_answer(replaces)
            await self.send(msg)
            self._start_answer(q, t0, detect_ms)

    # ---------- answer generation ----------
    def _start_answer(self, q: Question, t0: float, detect_ms: int | None) -> None:
        if self.answer_task and not self.answer_task.done():
            self.answer_task.cancel()  # newest question wins
        self.answer_task = asyncio.create_task(self._answer(q, t0, detect_ms))

    async def _answer(self, q: Question, t0: float, detect_ms: int | None) -> None:
        assert self.llm is not None
        timings: dict[str, int | None] = {"detect_ms": detect_ms, "retrieval_ms": q.retrieval_ms,
                                          "first_token_ms": None, "total_ms": None}
        stopped = False
        answer: list[str] = []
        messages = [
            ChatMessage("system", answer_system_for(q.text, self.cv_text, self.jd_text, self.session_type,
                                                    q.highlights)),
            ChatMessage("user", (f"Recent transcript:\n{format_history(list(self.history)) or '(none)'}\n\n"
                                 f"Question to answer ({q.category}):\n{q.text}")),
        ]
        await self.send({"type": "answer_start", "question_id": q.id})
        try:
            async for token in self.llm.stream_chat(messages, tier="answer"):
                if timings["first_token_ms"] is None:
                    timings["first_token_ms"] = ms(t0)
                answer.append(token)
                await self.send({"type": "token", "question_id": q.id, "text": token})
        except asyncio.CancelledError:
            stopped = True
            raise
        except LLMError as exc:
            stopped = True
            log.warning("answer generation failed: %s", exc)
            await self.send({"type": "error", "message": public_error(
                exc, "The answer couldn't be generated. Press Regenerate.")})
        except Exception:
            stopped = True
            log.exception("answer generation failed")
            await self.send({"type": "error", "message": "Answer generation failed. Press Regenerate."})
        finally:
            timings["total_ms"] = ms(t0)
            text = "".join(answer)
            # Numbers the CV does not contain are flagged so the candidate does not repeat them.
            unverified = unverified_numbers(text, [self.cv_text, q.text])
            # One JSON line per answer, for log-based monitoring; the same numbers feed the latency report.
            log.info("latency %s", json.dumps({"session": self.session_id, "question": q.id, "category": q.category,
                                               "stopped": stopped, "unverified": len(unverified), **timings}))
            if self.recorder and text.strip():
                self.recorder.answer(q.id, q.text, q.category, text, unverified, timings)
            await self.send({"type": "answer_done", "question_id": q.id, "stopped": stopped,
                             "timings": timings, "unverified": unverified})

    def regenerate(self) -> None:
        now = time.monotonic()
        if now - self.last_regenerate < REGENERATE_MIN_INTERVAL_S:
            return
        self.last_regenerate = now
        if self.current and self.llm:
            self._start_answer(self.current, time.perf_counter(), None)

    def stop_answer(self) -> None:
        if self.answer_task and not self.answer_task.done():
            self.answer_task.cancel()

    # ---------- plan usage ----------
    async def _check_allowance(self) -> bool:
        """Load the plan limit. Returns False (after telling the user) when no minutes are left."""
        users, history = self.services.users, self.services.history
        if users is None or history is None:
            return True  # no database: usage cannot be metered (development without Docker)
        user = await users.get(self.user_id)
        plan = plan_for(user.plan if user else None)
        self.limit_s = limit_seconds(plan)
        self.used_before_s = await history.used_seconds_this_month(self.user_id)
        if self.limit_s is not None and self.used_before_s >= self.limit_s:
            await self.send({"type": "error", "code": "limit",
                             "message": f"You have used all {plan.monthly_minutes} minutes on the {plan.name} plan "
                                        f"this month. Upgrade your plan to keep going."})
            return False
        return True

    def _record_usage(self) -> None:
        """Save the whole seconds used since the last save. Fractions carry over to the next save,
        so nothing is lost to rounding however often this runs."""
        whole = int(self.session_s)
        if self.recorder and whole > self.recorded_s:
            self.recorder.seconds(whole - self.recorded_s)
            self.recorded_s = whole

    def remaining_s(self) -> int | None:
        if self.limit_s is None:
            return None
        return max(0, int(self.limit_s - self.used_before_s - self.session_s))

    def _quiet(self) -> bool:
        answering = self.answer_task is not None and not self.answer_task.done()
        return (not answering and not self.detecting and self.turns.empty()
                and time.monotonic() - self.last_speech >= ROTATE_QUIET_S)

    async def _rotator(self, after_s: int) -> None:
        """Some hosts close a connection after a fixed time (Vercel: 300 s). Before that happens,
        at a moment when nobody is speaking and no answer is being written, ask the browser to
        open a fresh connection; it switches over before closing this one, so nothing is lost."""
        await asyncio.sleep(after_s)
        # Polling, not an Event: "quiet" means nothing happened for a while, which no single event marks.
        while not self._quiet():  # noqa: ASYNC110
            await asyncio.sleep(0.5)
        log.info("session %s: asking the browser to switch to a fresh connection", self.session_id)
        await self.send({"type": "rotate"})

    async def _meter(self) -> None:
        """Records time used and enforces the plan limit on the server (the browser cannot skip it)."""
        last = self.metered_since = time.monotonic()
        warned = False
        while True:
            await asyncio.sleep(METER_INTERVAL_S)
            now = time.monotonic()
            self.session_s += now - last
            last = now
            self._record_usage()
            remaining = self.remaining_s()
            await self.send({"type": "usage", "remaining_seconds": remaining})
            if remaining is None:
                continue
            if remaining <= 0:
                await self.send({"type": "error", "code": "limit",
                                 "message": "You have reached your plan's minutes for this month. "
                                            "The session has ended. Upgrade to keep going."})
                if self._request_end:
                    await self._request_end("limit")
                return
            if remaining <= LIMIT_WARNING_S and not warned:
                warned = True
                await self.send({"type": "warning",
                                 "message": f"About {max(1, round(remaining / 60))} minute(s) left on your plan "
                                            f"this month."})

    # ---------- lifecycle ----------
    async def _load_context(self, cv_id: str | None, jd_id: str | None,
                            session_type: str) -> tuple[str, str | None] | None:
        """Load the chosen CV/JD. Returns (cv title, jd title), or None after sending an error.
        A chosen CV that cannot be loaded is fatal: silently answering from the sample CV in a
        real interview would be worse than not starting."""
        self.session_type = session_type if session_type in ("behavioral", "technical", "mixed") else "mixed"
        if not cv_id and not jd_id:
            return "Sample CV", None
        repo = self.services.documents
        if repo is None:
            await self.send({"type": "error", "message": "Database is not available, so your CV cannot be "
                                                         "loaded. Check that Docker (Postgres) is running."})
            return None
        cv = await repo.get(self.user_id, cv_id) if cv_id else None
        jd = await repo.get(self.user_id, jd_id) if jd_id else None
        if (cv_id and (cv is None or cv.kind != "cv")) or (jd_id and (jd is None or jd.kind != "jd")):
            await self.send({"type": "error", "message": "The selected CV or job description was not found. "
                                                         "Choose it again and restart."})
            return None
        if cv:
            self.cv_text = cv.text
        self.jd_text = jd.text if jd else None
        docs = [d for d in (cv, jd) if d is not None]
        if self.services.retrieval is not None and docs:
            self.doc_ids = [d.id for d in docs]
            # Make sure the vectors exist (e.g. Pinecone was down at upload). Runs in the
            # background; until it finishes, answers use the CV text directly.
            self.tasks.append(asyncio.create_task(self._ensure_indexed(docs)))
        return (cv.title if cv else "Sample CV"), (jd.title if jd else None)

    async def _ensure_indexed(self, docs: list[Document]) -> None:
        assert self.services.retrieval is not None
        for d in docs:
            try:
                await self.services.retrieval.ensure_indexed(d)
            except Exception as exc:
                log.warning("could not index %s (%r); retrieval may return nothing", d.id, exc)

    async def _open_history(self, cv_title: str, jd_title: str | None, resume: str | None) -> bool:
        """Continue the interview a dropped connection was recording, or start a new one.
        Returns True when resumed."""
        history = self.services.history
        if history is None:
            return False
        try:
            resumed = (await history.resume_session(self.user_id, resume, self.connection_id, RESUME_WINDOW_S,
                                                    HISTORY_LINES)
                       if resume else None)
            if resume is not None and resumed is not None:
                self.session_id = resume
                self.asked = list(resumed.questions)
                self.history.extend(resumed.recent_lines)
                self.recorder = SessionRecorder(history, resume, self.connection_id, resumed.next_seq)
                log.info("session %s resumed by a new connection", resume)
                return True
            self.session_id = await history.create_session(self.user_id, self.session_type, cv_title, jd_title,
                                                           self.connection_id)
            self.recorder = SessionRecorder(history, self.session_id, self.connection_id)
        except Exception:
            log.exception("could not open the history record; continuing without history")
        return False

    async def start(self, sample_rate: int, cv_id: str | None = None, jd_id: str | None = None,
                    session_type: str = "mixed", resume: str | None = None) -> None:
        if self.started:
            return
        self.started = True
        self.sample_rate = sample_rate
        settings = get_settings()
        loaded = await self._load_context(cv_id, jd_id, session_type)
        if loaded is None or not await self._check_allowance():
            return
        cv_title, jd_title = loaded
        try:
            stt = self.stt = get_stt(settings)
        except Exception as exc:
            log.error("transcription provider unavailable: %s", exc)
            await self.send({"type": "error", "message": public_error(
                exc, "Transcription isn't available right now. Try again in a few minutes.")})
            return
        try:
            self.llm = get_llm(settings)
        except Exception as exc:
            self.llm = None
            log.error("answer provider unavailable: %s", exc)
            await self.send({"type": "warning", "message": "Answers disabled: " + public_error(
                exc, "the answer service is unavailable right now. The transcript still works.")})

        # Both streams connect at the same time: on a slow network each can take several attempts.
        async def open_one(speaker: Speaker) -> STTStream | BaseException:
            try:
                return await stt.open_stream(speaker, sample_rate=sample_rate)
            except Exception as exc:
                return exc

        speakers: tuple[Speaker, Speaker] = ("interviewer", "candidate")
        outcomes = await asyncio.gather(*(open_one(sp) for sp in speakers))
        opened: list[Speaker] = []
        failed: dict[Speaker, BaseException] = {}
        for speaker, outcome in zip(speakers, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                log.error("could not open STT stream for %s: %r", speaker, outcome)
                failed[speaker] = outcome
            else:
                self.streams[speaker] = outcome
                opened.append(speaker)
        if "interviewer" in failed:
            # Without the interviewer's words there is nothing to answer: stop before anything is
            # recorded or counted against the plan.
            await self.send({"type": "error", "message": "Could not start transcription of the interviewer: "
                             + public_error(failed["interviewer"], "try again in a minute.")})
            for stream in list(self.streams.values()):
                try:
                    await stream.close()
                except Exception:
                    log.exception("Error closing STT stream")
            self.streams.clear()
            return
        if "candidate" in failed:
            await self.send({"type": "warning", "message": "Could not start transcription of you: "
                             + public_error(failed["candidate"], "only the interviewer will be transcribed.")})

        resumed = await self._open_history(cv_title, jd_title, resume)
        # Forwarding starts only once the history record exists, so no line is missed.
        for speaker in opened:
            self.tasks.append(asyncio.create_task(self._transcribe(speaker, self.streams[speaker])))
        self.meter_task = asyncio.create_task(self._meter())
        if settings.session_rotate_s > 0:
            self.rotate_task = asyncio.create_task(self._rotator(settings.session_rotate_s))
        if self.llm is not None:
            self.detector_task = asyncio.create_task(self._detector())
        await self.send({"type": "ready", "channels": opened, "answers": self.llm is not None,
                         "cv": cv_title, "jd": jd_title, "session_id": self.session_id, "resumed": resumed,
                         "remaining_seconds": self.remaining_s()})

    async def audio(self, data: bytes) -> None:
        if len(data) < 2:
            return
        speaker = CHANNELS.get(data[0])
        stream = self.streams.get(speaker) if speaker else None
        if stream is not None:
            await stream.send_audio(data[1:])

    async def close(self) -> None:
        # Stop answering and detecting first; then let the STT streams flush their final turns.
        # The finally block guarantees resources are released even if this cleanup is itself
        # cancelled (e.g. an abrupt client disconnect).
        self.closing = True
        for task in (self.detector_task, self.meter_task, self.rotate_task):
            if task:
                task.cancel()
        self.stop_answer()
        try:
            for stream in list(self.streams.values()):
                try:
                    await stream.close()
                except Exception:
                    log.exception("Error closing STT stream")
            pending = [t for t in self.tasks if not t.done()]
            if pending:
                await asyncio.wait(pending, timeout=5)
            if self.answer_task:
                await asyncio.wait([self.answer_task], timeout=2)  # lets the stopped answer be recorded
        finally:
            for t in self.tasks:
                t.cancel()
            if self.llm is not None:
                await asyncio.shield(self.llm.aclose())
            if self.recorder:
                # Time since the last meter tick still counts towards the plan (rounded up, so a
                # session can never be used for free by ending it between ticks).
                if self.metered_since is not None:
                    self.session_s = time.monotonic() - self.metered_since
                self.session_s = float(math.ceil(self.session_s))
                self._record_usage()
                await asyncio.shield(self.recorder.close())
