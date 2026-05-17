"""Untrusted-input helpers.

Self-contained — does not import from the marketplace repo. Mirrors the
patterns the marketplace applies at its own write boundaries
(check_injection + strip_html in app/trust/content_filter.py, and
_scrub_for_prompt in app/trust/behavioral_grader.py per the 2026-05
security audit). Partners forking this template inherit the same
defenses.
"""

from __future__ import annotations

import re

# ASCII control chars except newline (\x0a) and tab (\x09).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Zero-width and bidi tricks. Specifically:
#   U+200B  zero-width space
#   U+200C  zero-width non-joiner
#   U+200D  zero-width joiner
#   U+2060  word joiner
#   U+FEFF  byte order mark / zero-width no-break space
_ZERO_WIDTH_RE = re.compile(r"[​‌‍⁠﻿]")

#   U+202A..U+202E  bidi formatting (LRE, RLE, PDF, LRO, RLO)
#   U+2066..U+2069  isolate / pop-directional-isolate
_BIDI_OVERRIDE_RE = re.compile(r"[‪-‮⁦-⁩]")

# Conservative HTML tag removal. No parser dependency. Designed for prose,
# not for code blocks (code blocks containing `<` will be partially eaten;
# the caller is responsible for not feeding code into these helpers).
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Tag-like sequences for the stricter prompt scrub. Greedy enough to
# catch `<system>`, `<|im_start|>`, `<role>` etc., but bounded so we
# don't pathologically chew through plain prose containing `<`.
_TAGLIKE_RE = re.compile(r"<\s*/?\s*[a-zA-Z|][a-zA-Z0-9|_\-\s/=\"']*\s*/?>")

# Suspicious instruction markers. Lowercased substrings; check_injection
# does a case-insensitive contains-any check. False positives are fine —
# the caller decides what to do with the True signal (skip, log, scrub).
# Missed positives are NOT fine: keep this list conservative-broad.
_INJECTION_MARKERS: tuple[str, ...] = (
    "ignore previous instructions",
    "ignore the previous instructions",
    "ignore all previous instructions",
    "ignore the above",
    "disregard the above",
    "disregard previous",
    "disregard the previous",
    "ignore your instructions",
    "ignore all prior",
    "forget your previous",
    "forget the previous",
    "system:",
    "system prompt:",
    "new instructions:",
    "override the",
    "override your",
    "you are now",
    "you are no longer",
    "as an ai language model",
    "[inst]",
    "[/inst]",
    "<|im_start|>",
    "<|im_end|>",
    "developer mode",
    "dan mode",
    "jailbreak",
    "pretend you are",
    "act as",
)


def strip_control_chars(text: str) -> str:
    """Remove ASCII control characters and unicode bidi / zero-width tricks.

    Newline and tab are preserved; all other C0 controls are stripped.
    Zero-width and bidi-override codepoints are stripped — they have no
    legitimate use in prose and are a known prompt-injection vector.
    """
    if not text:
        return text
    text = _CONTROL_CHAR_RE.sub("", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _BIDI_OVERRIDE_RE.sub("", text)
    return text


def strip_html(text: str) -> str:
    """Conservative HTML-tag removal. Preserves text content.

    Intentionally not a full HTML parser — this template ships zero
    transitive HTML deps. For prose fields, the regex is sufficient.
    """
    if not text:
        return text
    return _HTML_TAG_RE.sub("", text)


def check_injection(text: str) -> bool:
    """True if `text` contains any known instruction-marker substring.

    Case-insensitive. Designed for signaling — the caller decides whether
    a True result means reject, log-and-proceed, or scrub. Conservative
    by design; if you find a missed positive, add the marker to
    _INJECTION_MARKERS.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in _INJECTION_MARKERS)


def sanitize_input(text: str, max_len: int = 10_000) -> str:
    """Standard pipeline for untrusted text at trust boundaries.

    1. strip_control_chars (C0, zero-width, bidi)
    2. strip_html (conservative tag removal)
    3. enforce max_len (silent truncation)

    Does NOT call check_injection — caller decides whether the presence
    of an injection marker means reject or scrub.
    """
    if not text:
        return text
    text = strip_control_chars(text)
    text = strip_html(text)
    if len(text) > max_len:
        text = text[:max_len]
    return text


def scrub_for_prompt(text: str) -> str:
    """Stricter cleanup, for content interpolated into LLM prompts.

    Mirrors the marketplace's app/trust/behavioral_grader._scrub_for_prompt
    pattern: strip control chars, then remove HTML/XML-like tag sequences
    entirely so user content cannot terminate the surrounding delimiter
    block or inject role-confusion markers like <|im_start|>.
    Remaining lone angle brackets are dropped as well.
    """
    if not text:
        return text
    text = strip_control_chars(text)
    text = _TAGLIKE_RE.sub("", text)
    # Drop any remaining lone < or > so user text can never look like the
    # opening of a prompt delimiter to a downstream tokenizer.
    text = text.replace("<", "").replace(">", "")
    return text
