"""Evaluate question detection and answers on the test question set, against the real providers.

Run from the backend folder (virtualenv active):
    python -m eval.run_eval              # detection + answers
    python -m eval.run_eval --answers    # answers only

Uses Groq (GROQ_API_KEY) and, if PINECONE_API_KEY is set, the real embeddings + Pinecone retrieval.
The test CV/JD are indexed under a separate "eval-user" namespace, so real user data is untouched.
Writes eval/report.md. Takes a few minutes: the free Groq tier allows ~8k tokens/minute, so the
script waits and retries when it hits the rate limit.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import time
from pathlib import Path

from app.config import get_settings
from app.copilot.detector import detect_question
from app.copilot.factcheck import unverified_numbers
from app.copilot.prompts import answer_system_for
from app.documents.repo import Document
from app.providers import load_providers
from app.providers.base import ChatMessage, LLMError
from app.providers.registry import get_embedder, get_llm, get_vector_store
from app.retrieval.service import RetrievalService
from eval.cases import ANSWER_CASES, DETECTION_CASES

HERE = Path(__file__).resolve().parent
CV = (HERE / "test_cv.txt").read_text(encoding="utf-8")
JD = (HERE / "test_jd.txt").read_text(encoding="utf-8")
STAR = ["**Situation", "**Task", "**Action", "**Result"]
EVAL_USER = "eval-user"


async def with_retry(fn, label: str):
    for attempt in range(6):
        try:
            return await fn()
        except LLMError as exc:
            if "rate limit" not in str(exc).lower() or attempt == 5:
                raise
            print(f"   rate limited on {label}; waiting 20 s")
            await asyncio.sleep(20)


async def run_detection(llm) -> tuple[int, list[str]]:
    passed, rows = 0, ["## Question detection", "", "| # | Expected | Got | Pass | Turn |", "|---|---|---|---|---|"]
    for i, (turn, expect, words) in enumerate(DETECTION_CASES, 1):
        found = await with_retry(lambda turn=turn: detect_question(llm, [], turn, []), f"detection {i}")
        got_q = found is not None
        ok = got_q == expect and (not expect or all(w.lower() in found.question.lower() for w in words))
        passed += ok
        got = f"{found.question} ({found.category})" if found else "no question"
        rows.append(f"| {i} | {'question' if expect else 'none'} | {got} | {'✅' if ok else '❌'} | {turn[:70]}... |")
        print(f"detect {i:2}: {'PASS' if ok else 'FAIL'}  {got}")
        await asyncio.sleep(1)
    return passed, rows


def check_answer(answer: str, kind: str, facts: list[str]) -> dict[str, bool]:
    words = len(re.findall(r"\w+", answer))
    has_star = all(label in answer for label in STAR)
    checks = {
        "no invented numbers": not unverified_numbers(answer, [CV]),
        "bold first line": answer.strip().startswith("**"),
        "under 170 words": words <= 170,
    }
    if kind == "story":
        checks["STAR format"] = has_star
    if kind == "motivation":
        checks["no STAR"] = "**Situation" not in answer
    if kind == "technical":
        checks["technical under 130 words"] = words <= 130
    if facts:
        checks["uses CV facts"] = any(f.lower() in answer.lower() for f in facts)
    elif kind in ("story", "weakness", "salary"):  # the CV has no such content: must not invent it
        checks["uses [placeholders], no invented event"] = "[" in answer
    return checks


async def run_answers(llm, retrieval: RetrievalService | None) -> tuple[int, int, list[str], list[int]]:
    doc_ids: list[str] = []
    if retrieval is not None:
        for doc_id, kind, text in (("eval-cv", "cv", CV), ("eval-jd", "jd", JD)):
            await retrieval.index_document(Document(doc_id, EVAL_USER, kind, doc_id, text))
            doc_ids.append(doc_id)
    passed = total = 0
    latencies: list[int] = []
    rows = ["## Answers", ""]
    for i, (question, category, kind, facts) in enumerate(ANSWER_CASES, 1):
        highlights: list[str] = []
        if retrieval is not None:
            hits = await retrieval.retrieve(EVAL_USER, question, doc_ids, top_k=4)
            highlights = [h.text for h in hits]
        system = answer_system_for(question, CV, JD, "mixed", highlights)  # same routing as live sessions
        messages = [ChatMessage("system", system),
                    ChatMessage("user", f"Recent transcript:\n(none)\n\nQuestion to answer ({category}):\n{question}")]

        async def generate(messages=messages):
            t0, first, parts = time.perf_counter(), None, []
            async for tok in llm.stream_chat(messages, tier="answer"):
                first = first or int((time.perf_counter() - t0) * 1000)
                parts.append(tok)
            return "".join(parts), first

        answer, first_ms = await with_retry(generate, f"answer {i}")
        latencies.append(first_ms or 0)
        checks = check_answer(answer, kind, facts)
        ok = sum(checks.values())
        passed += ok
        total += len(checks)
        status = "PASS" if ok == len(checks) else "FAIL"
        failed = [k for k, v in checks.items() if not v]
        note = ("failed: " + ", ".join(failed)) if failed else ""
        print(f"answer {i:2}: {status}  {question}  first token {first_ms} ms  {note}")
        rows += [f"### {i}. {question}  ({kind}) {'✅' if status == 'PASS' else '❌'}", "",
                 "Checks: " + ", ".join(f"{k} {'✅' if v else '❌'}" for k, v in checks.items()), "",
                 f"First token: {first_ms} ms. Retrieved passages: {len(highlights)}", "",
                 *(f"> {line}" for line in answer.strip().splitlines()), ""]
        if unverified := unverified_numbers(answer, [CV]):
            rows.append(f"**Numbers not in CV:** {', '.join(unverified)}\n")
        await asyncio.sleep(2)
    return passed, total, rows, latencies


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", action="store_true", help="answers only")
    args = parser.parse_args()

    load_providers()
    settings = get_settings()
    llm = get_llm(settings)
    retrieval = None
    try:
        retrieval = RetrievalService(get_embedder(settings), get_vector_store(settings))
        print("retrieval: real embeddings + Pinecone (first run downloads the embedding model)")
    except Exception as exc:
        print(f"retrieval disabled ({exc}); evaluating with the full CV only")

    report = ["# Evaluation report", "", f"Answer model: {settings.groq_answer_model}, "
              f"detection model: {settings.groq_fast_model}", ""]
    try:
        if not args.answers:
            d_pass, d_rows = await run_detection(llm)
            report += [f"**Detection: {d_pass}/{len(DETECTION_CASES)} correct**", "", *d_rows, ""]
            print(f"\nDETECTION: {d_pass}/{len(DETECTION_CASES)} correct\n")
        a_pass, a_total, a_rows, lat = await run_answers(llm, retrieval)
        lat_sorted = sorted(lat)
        summary = (f"**Answers: {a_pass}/{a_total} checks passed.** First token: median "
                   f"{lat_sorted[len(lat) // 2]} ms, slowest {lat_sorted[-1]} ms (answer step only, "
                   f"excludes speech-to-text and detection).")
        report += [summary, "", *a_rows]
        print(f"\nANSWERS: {a_pass}/{a_total} checks passed; median first token {lat_sorted[len(lat) // 2]} ms")
    finally:
        await llm.aclose()
        if retrieval is not None:
            await retrieval.aclose()
    (HERE / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"report written to {HERE / 'report.md'}")


if __name__ == "__main__":
    asyncio.run(main())
