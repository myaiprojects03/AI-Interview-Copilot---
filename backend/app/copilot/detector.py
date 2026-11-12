"""Question detection: a cheap cue filter, then a fast LLM call that extracts the actual question.

Why two stages: an end of turn is not the same as an end of question. Interviewers pause mid-question,
ask questions phrased as statements, and turns can include the start of the candidate's reply.
The cue filter skips obvious non-questions (saving latency and free-tier quota); the fast model
extracts and cleans the question, merges continuations, and classifies it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.copilot.prompts import DETECT_SYSTEM, format_history
from app.providers.base import ChatMessage, LLMProvider

_CUES = re.compile(
    r"\?|\b("
    r"tell (me|us)|walk (me|us) through|describe|explain|talk (me |us )?(about|through)|"
    r"give (me|us) an example|share an example|elaborate|"
    r"how (would|do|did|can|could|have|will|are|is)|"
    r"what (is|are|was|were|would|do|did|makes|motivates|interests|excites|attracted)|"
    r"why (do|did|would|are|should|is|were)|"
    r"can you|could you|would you|will you|have you|do you|did you|are you|were you|"
    r"when (did|have|was|were|would)|where do you see|which"
    r")\b",
    re.IGNORECASE,
)

CATEGORIES = {"behavioral", "technical", "other"}


@dataclass
class DetectedQuestion:
    question: str
    category: str


def has_question_cue(text: str) -> bool:
    return bool(_CUES.search(text))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", text.lower())).strip()


def is_duplicate(question: str, previous: list[str], threshold: float = 0.75) -> bool:
    """True if the question is the same as one already answered, allowing for rewording
    (e.g. caught once mid-turn and again when the turn ends)."""
    q = normalize(question)
    for p in previous:
        n = normalize(p)
        if q == n or SequenceMatcher(None, q, n).ratio() >= threshold:
            return True
    return False


async def detect_question(llm: LLMProvider, history: list[tuple[str, str]], latest: str,
                          already: list[str] | None = None) -> DetectedQuestion | None:
    """Return the newest unanswered question in the latest interviewer turn, or None."""
    if not has_question_cue(latest):
        return None
    done = "\n".join(f"- {q}" for q in (already or [])[-5:]) or "(none)"
    user = (
        f"Recent transcript (oldest first):\n{format_history(history) or '(none)'}\n\n"
        f"Questions already detected (do not return these again):\n{done}\n\n"
        f"Latest interviewer turn:\n{latest}"
    )
    result = await llm.complete_json(
        [ChatMessage("system", DETECT_SYSTEM), ChatMessage("user", user)], tier="fast"
    )
    if not result.get("is_question"):
        return None
    question = str(result.get("question") or "").strip()
    if len(question) < 5:
        return None
    category = str(result.get("category", "other")).lower()
    return DetectedQuestion(question, category if category in CATEGORIES else "other")
