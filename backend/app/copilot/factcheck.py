"""Flag numbers in an answer that do not appear in the candidate's CV / job description.

The prompt forbids invented figures, but a prompt rule lowers the risk without removing it.
This check runs on every finished answer, so the candidate sees a warning before repeating
a number that is not in their own CV.
"""
from __future__ import annotations

import re

# A number with optional currency / thousands separators / decimals and an optional scale or percent suffix.
_NUMBER = re.compile(
    r"(?<![\w.])[$£€]?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?(k|m|bn|b|million|billion|thousand|%|percent)?(?![\w])",
    re.IGNORECASE,
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


def _values(text: str) -> list[tuple[str, float, float, bool]]:
    """(display text, raw value, scaled value, is_percent) for every number in the text."""
    out = []
    for m in _NUMBER.finditer(text):
        whole, frac, suffix = m.group(1).replace(",", ""), m.group(2), (m.group(3) or "").lower()
        raw = float(f"{whole}.{frac}") if frac else float(whole)
        out.append((m.group(0).strip(), raw, raw * _SCALE.get(suffix, 1), suffix in ("%", "percent")))
    return out


def unverified_numbers(answer: str, sources: list[str]) -> list[str]:
    """Numbers in the answer whose value appears in none of the sources ("$2M" matches "2M",
    "$2,000,000" and "2 million"). A percentage must match a percentage: "30%" is not supported
    by "30 members". Written-out numbers ("four years") are not checked."""
    any_value: set[float] = set()
    percents: set[float] = set()
    for src in sources:
        for _, raw, scaled, is_pct in _values(src):
            any_value.update((raw, scaled))
            if is_pct:
                percents.add(raw)
    flagged: list[str] = []
    for shown, raw, scaled, is_pct in _values(answer):
        ok = raw in percents if is_pct else (raw in any_value or scaled in any_value)
        if not ok and shown not in flagged:
            flagged.append(shown)
    return flagged
