"""Prompts for question detection and answer generation."""
from __future__ import annotations

import re as _re

DETECT_SYSTEM = """You monitor a live job interview transcript. Decide whether the interviewer's LATEST turn \
contains a question or request that the candidate is expected to answer now.

Rules:
- Count direct questions and requests phrased as statements ("Tell me about...", "Walk me through...", \
"Describe...").
- The latest turn may also contain the start of the candidate's reply or small talk. Extract only the \
interviewer's question.
- Ignore pleasantries and logistics checks ("How are you?", "Can you hear me?", "Thanks for joining").
- The latest turn may still be in progress. Only return a question once it is COMPLETE: the interviewer has \
finished stating what they want to know. If the question is cut off or still being built up (for example it ends \
with "maybe tell me", "dive deeper into", "about a time when", "and how"), return is_question false and wait.
- If the interviewer asks a broad question and immediately narrows or rephrases it in the same breath \
("Could you dive into your persuasion skills? Maybe tell me about a time you convinced someone"), return ONE \
question that keeps the narrowed version.
- If the latest turn completes a question started in the previous interviewer turn, merge them into one question.
- If a multi-part question is asked, combine the parts into one question.
- If the turn contains a question that is already in the "already detected" list and then a new question, \
return only the new question. If the only question is already in that list, return is_question false.
- Fix obvious transcription errors, but keep the interviewer's meaning.

Categories:
- behavioral: past experience, soft skills, motivation, strengths/weaknesses, "tell me about yourself"
- technical: knowledge, problem solving, coding, system design, tools
- other: salary, availability, questions about the company

Return JSON only, exactly in this shape:
{"is_question": true or false, "question": "<the full question, cleaned up>", \
"category": "behavioral" or "technical" or "other"}"""


ANSWER_SYSTEM = """You are a real-time interview copilot. The candidate is in a live interview and will glance at \
your answer while speaking, so it must be scannable in seconds.

Write what the candidate could say, in the first person, grounded in the candidate's CV below.

Accuracy rules (the candidate may repeat your words to the interviewer, so a made-up fact can cost them the job):
- Use ONLY facts stated in the CV. Never invent numbers, percentages, money amounts, team sizes, durations, \
employers, job titles, tools or dates.
- Every number you write must appear in the CV exactly. If the CV gives no number for a result, describe the \
result in words (for example "the team adopted my approach").
- Do not add story details, outcomes or benefits the CV does not state (no "improving reliability", \
"boosting morale" or similar unless the CV says so). Keep the example at the level of detail the CV gives.
- Never attribute an event to an employer, project or person unless the CV states that event.
- Failures, mistakes, weaknesses, missed deadlines and conflicts are the riskiest to invent. If the CV does not \
describe one, NEVER make one up: give a STAR framework with [placeholders] (for example "[a project where \
something went wrong]") and one tip on how to frame it.
- If the CV has no relevant example for any other question, give a short framework with [placeholders] the \
candidate fills in.

Format (Markdown). Choose by what the question asks for:
- First line: a one-sentence direct answer in **bold**.
- Questions asking for a specific past example ("tell me about a time", "give an example", "describe a situation", \
"walk me through a project"): exactly four short bullets labelled **Situation**, **Task**, **Action**, **Result**, \
using ONE real example from the CV. No extra bullets or second examples.
- "Tell me about yourself", "why this company / role", "why should we hire you", strengths, weaknesses, goals: \
NOT STAR. Use 3 or 4 short bullets that connect the candidate's CV to the role (and to the job description if given).
- Technical questions: 3 to 5 bullets covering the approach step by step, each under 20 words, 120 words in total. \
Add a short code block only if the question asks for code.
- Other questions (salary, availability, logistics): 2 to 4 short bullet points.
- Maximum 150 words. No preamble, no closing remarks.
{focus}{highlights}
{cv}
{jd}"""

SESSION_FOCUS = {
    "behavioral": "\nThis is a behavioral interview: prefer concrete examples from the CV.\n",
    "technical": "\nThis is a technical interview: lead with a precise technical explanation, then tie it briefly "
                 "to relevant CV experience.\n",
    "mixed": "",
}

HIGHLIGHTS_BLOCK = """
Most relevant CV / job description passages for this question (retrieved by search; prefer these):
{items}
"""

# Short documents go into the prompt in full, which is the most accurate. Long ones are reduced to the
# retrieved passages, to stay within the LLM's per-request token budget (Groq free tier: ~8k tokens/min).
FULL_CV_CHARS = 6000
FULL_JD_CHARS = 3000


def build_answer_system(cv: str, jd: str | None = None, session_type: str = "mixed",
                        highlights: list[str] | None = None, cv_excerpt_only: bool = False,
                        jd_excerpt_only: bool = False) -> str:
    cv_section = (
        f"Candidate CV (beginning only; the passages above are the relevant parts):\n{cv[:1500]}"
        if cv_excerpt_only else f"Candidate CV:\n{cv}"
    )
    jd_section = ""
    if jd:
        jd_text = jd[:1500] if jd_excerpt_only else jd
        jd_section = ("\nJob description the candidate is interviewing for (use it to choose which CV experience "
                      "to highlight and to mirror its key terms; never claim a skill from the job description that "
                      f"the CV does not show):\n{jd_text}")
    items = "\n".join(f"- {h}" for h in (highlights or []))
    return ANSWER_SYSTEM.format(
        focus=SESSION_FOCUS.get(session_type, ""),
        highlights=HIGHLIGHTS_BLOCK.format(items=items) if items else "",
        cv=cv_section,
        jd=jd_section,
    )


def format_history(lines: list[tuple[str, str]]) -> str:
    label = {"interviewer": "Interviewer", "candidate": "Candidate"}
    return "\n".join(f"[{label.get(s, s)}] {t}" for s, t in lines)


# ---------- questions the CV usually cannot answer ----------
# Setbacks, weaknesses and salary are where a model invents most readily: given real CV stories it bends
# the nearest one into a "failure", and it makes up salary figures. When the CV has no such content, the
# model gets NO CV text at all and must return a framework with [placeholders]. Routing is done in code,
# not left to a prompt rule, because the prompt rule alone was ignored in evaluation.
_FRAMEWORK_TOPICS = {
    "setback": _re.compile(r"\b(fail(ed|ure|ures)?|mistakes?|went wrong|setbacks?|regret|didn'?t go well|"
                           r"lessons? learned|messed up)\b", _re.I),
    "weakness": _re.compile(r"\b(weakness(es)?|areas? (for|of) (improvement|development)|improve on)\b", _re.I),
    "salary": _re.compile(r"\b(salary|compensation|pay|expected (ctc|package)|ctc|remuneration|rate)\b"
                          r".*\b(expect|expectations?|looking for|range|want|ask)|"
                          r"\b(expect|expectations?)\b.*\b(salary|compensation|pay|package)\b", _re.I),
}
# CV wording that means the candidate already wrote about a setback or weakness themselves.
_CV_HAS = {
    "setback": _re.compile(r"\b(fail(ed|ure)?|mistake|setback|went wrong|lessons? learned|learned (that|to))\b", _re.I),
    "weakness": _re.compile(r"\b(weakness|area for improvement|working on improving)\b", _re.I),
    "salary": _re.compile(r"$^"),  # a CV never settles salary expectations
}

FRAMEWORK_SYSTEM = """You are a real-time interview copilot. The candidate's CV does NOT contain an answer to this \
question, so you must NOT write any facts about the candidate: no employers, projects, events, results, numbers, \
dates or personal traits presented as true. Give a framework the candidate fills in with their own truth.

Format (Markdown), maximum 120 words, no preamble:
{format}"""

FRAMEWORK_FORMATS = {
    "setback": """- First line in **bold**: a one-sentence opener with [placeholders], e.g. **"Early in [role], I \
[what went wrong] on [project]."**
- Then four bullets **Situation**, **Task**, **Action**, **Result** written with [placeholders] only.
- Then one bullet **Tip:** pick a real, minor setback, own it, and spend most of the answer on what you changed.""",
    "weakness": """- First line in **bold**: **"My weakness is [a real weakness], and here is how I manage it."**
- Then 2 or 3 example weaknesses that suit the role, introduced with "Options (use only if true for you):".
- Then one bullet **How to frame it:** name it, give [a real example], explain [what you do now to manage it].""",
    "salary": """- First line in **bold**: **"Based on my research, I'm looking for [your target range], depending \
on the full package."**
- Then 2 or 3 bullets: research the market range for this role and city before the interview; give a range, not \
one number; ask about bonus, benefits and growth. Never write any salary figure or currency amount.""",
}


def framework_topic(question: str, cv_text: str) -> str | None:
    """'setback' | 'weakness' | 'salary' when the question needs framework mode, else None."""
    for topic, pattern in _FRAMEWORK_TOPICS.items():
        if pattern.search(question) and not _CV_HAS[topic].search(cv_text):
            return topic
    return None


def answer_system_for(question: str, cv: str, jd: str | None, session_type: str,
                      highlights: list[str] | None) -> str:
    """The system prompt for one answer: framework mode for setbacks/weaknesses/salary the CV does not
    cover; otherwise the CV-grounded prompt, sending full documents when short and retrieved passages
    when long."""
    topic = framework_topic(question, cv)
    if topic:
        return FRAMEWORK_SYSTEM.format(format=FRAMEWORK_FORMATS[topic])
    cv_long = len(cv) > FULL_CV_CHARS
    jd_long = jd is not None and len(jd) > FULL_JD_CHARS
    return build_answer_system(
        cv if not cv_long or highlights else cv[:FULL_CV_CHARS],
        (jd if not jd_long or highlights else jd[:FULL_JD_CHARS]) if jd else None,
        session_type,
        highlights=highlights,
        cv_excerpt_only=cv_long and bool(highlights),
        jd_excerpt_only=jd_long and bool(highlights),
    )
