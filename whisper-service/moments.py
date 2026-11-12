"""Content-based frame selection: where the speaker points at the screen.

Picking frames at scene changes, or evenly, knows nothing about the video. If
the speaker says "look, here's the 65K level" at 3:12, that moment lands on a
frame only by chance. This module reads the timestamped transcript and finds
the moments where the speaker refers to something on screen ("look at",
"this level", Romanian "aici", "nivelul", Hungarian "itt", "szint"…) or says
numbers, and returns those seconds. Cutting the frames is up to the caller.

The Python original of this workflow uses the same code; keep them in step.
"""

from __future__ import annotations

import re

# Phrases a speaker uses when pointing at the screen. English, Romanian, Hungarian.
_CUES = [
    # English
    r"\blook(ing)? at\b", r"\byou can see\b", r"\bas you see\b", r"\bon the (chart|screen)\b",
    r"\bthis (chart|level|line|area|zone|box|candle|trend ?line|range|pattern|channel)\b",
    r"\bthe (chart|weekly|daily|monthly) (chart|candle|close)\b", r"\bright here\b",
    r"\bover here\b", r"\bhere we\b", r"\bsupport\b", r"\bresistance\b", r"\btarget\b",
    r"\btrend ?line\b", r"\bmoving average\b", r"\b\d{2,3}[- ]?(day|week)\b",
    r"\bi('ve| have)? (drew|drawn|marked|highlighted)\b",
    # Romanian
    r"\baici\b", r"\buita(ți|ti)\b", r"\buite\b", r"\bvede(ți|ti)\b", r"\bvezi\b",
    r"\bgraficul\b", r"\bpe grafic\b", r"\bnivelul\b", r"\blinia\b", r"\bzona\b",
    r"\bsuport(ul)?\b", r"\brezisten(ța|ta|ță)\b", r"\bținta\b|\btinta\b",
    r"\bmedia mobil", r"\bam desenat\b", r"\bam marcat\b",
    # Hungarian
    r"\bitt\b", r"\bnézzétek\b", r"\blátható\b", r"\blátjátok\b", r"\bgrafikon",
    r"\bszint", r"\bvonal", r"\btámasz", r"\bellenállás", r"\bcélár",
]
_CUE_RES = [re.compile(c, re.IGNORECASE) for c in _CUES]

# Numbers that may be price levels or percentages: $65,000, 65k, 81,700,
# 4.5%, 30 bps, "65 thousand", "65 de mii", "65 ezer".
_NUMBER = re.compile(
    r"\$\s?\d[\d,.]*\s?[kKmMbB]?"
    r"|\b\d{1,3}(?:[,.]\d{3})+\b"
    r"|\b\d+(?:[.,]\d+)?\s?(?:k|K|%|percent|bps|bp)\b"
    r"|\b\d{1,3}\s?(?:thousand|de mii|mii|ezer)\b",
    re.IGNORECASE,
)

# Seconds of speech scored together: one thought rarely fits in one caption line.
WINDOW_SEC = 12.0
# The chart usually appears a moment after the sentence starts.
LEAD_SEC = 2.0
# A moment needs at least one on-screen reference or two numbers.
MIN_SCORE = 2


def segment_score(text: str) -> int:
    cues = sum(1 for cue in _CUE_RES if cue.search(text))
    numbers = len(_NUMBER.findall(text))
    return 2 * min(cues, 3) + min(numbers, 3)


def pick_moments(
    segments: list[tuple[float, str]],
    count: int,
    duration: float | None = None,
) -> list[float]:
    """Up to `count` seconds where the speaker points at something on screen.

    `segments`: [(start second, text), ...] from the transcript. Returns the
    moments in time order, or an empty list if the transcript has none.
    """
    if count <= 0 or not segments:
        return []
    segs = sorted((float(start), str(text or "")) for start, text in segments)
    end = float(duration) if duration else segs[-1][0] + 5.0
    scores = [segment_score(text) for _, text in segs]

    windows: list[tuple[int, float]] = []
    for i, (start, _) in enumerate(segs):
        if scores[i] <= 0:
            continue
        total, k = 0, i
        while k < len(segs) and segs[k][0] < start + WINDOW_SEC:
            total += scores[k]
            k += 1
        windows.append((total, start))
    # Strongest moments first; on a tie, the earlier one.
    windows.sort(key=lambda w: (-w[0], w[1]))

    # Keep the frames apart, so they don't all pile up in one place.
    gap = max(15.0, end / (count * 4))
    picked: list[float] = []
    for total, start in windows:
        if total < MIN_SCORE:
            break
        moment = min(start + LEAD_SEC, max(0.0, end - 1.0))
        if all(abs(moment - p) >= gap for p in picked):
            picked.append(moment)
            if len(picked) == count:
                break
    return sorted(picked)


def content_share(max_frames: int) -> int:
    """How many frames to pick by content; the rest come from scene changes."""
    return max_frames - max_frames // 3


def context_at(segments: list[tuple[float, str]], second: float, window: float = 15.0) -> str:
    """What is said around a moment, so the model can tie a chart to a sentence."""
    return " ".join(
        text.strip() for start, text in segments if second - window <= start <= second + window
    ).strip()
