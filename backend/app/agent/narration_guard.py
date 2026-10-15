"""Pure guard for narration text and proposal risk notes (spec §4.1, §4.2).

Hard violations (empty, markup, fourth_wall, prompt_echo, script) force a
retry and, if they persist, the fallback line. Soft ones (too_long,
unknown_name) only steer which attempt is kept. No LLM, no I/O beyond the
cached setting YAML loads.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

from app.game.schemas import ColonyState
from app.game.setting_loader import Setting, list_available_settings, load_setting

HARD_CODES = frozenset({"empty", "markup", "fourth_wall", "prompt_echo", "script"})
RISK_NOTE_MAX = 120
MAX_CHARS = 700
MAX_SENTENCES = 4
MAX_NON_LATIN_SHARE = 0.2

_MARKUP = re.compile(r"<[A-Za-z/][^>]*>")
_FOURTH_WALL = re.compile(r"\bthe player\b", re.IGNORECASE)
_ECHO_PREFIXES = ("now write", "write a", "context:", "characters:", "recap", "keep tone")
# Spec §4.2: word-boundary anchors so "iPhone" and "McKay" yield no name token.
_TOKEN = re.compile(r"\b[A-Z][a-z]+(?:[-'][A-Za-z]+)*\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STAT_WORDS = re.compile(r"\b(legitimacy|health|grievance)\b", re.IGNORECASE)
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
_INITIAL_AFTER = set(".!?:;\n—–")
_STOP = frozenset(
    {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "Day",
        "Turn",
        "God",
    }
)


@dataclass(frozen=True)
class Violation:
    code: str

    @property
    def kind(self) -> str:
        return self.code.split(":", 1)[0]

    @property
    def hard(self) -> bool:
        return self.kind in HARD_CODES


def _normalize(text: str) -> str:
    return text.translate(_QUOTES)


def _tokens(text: str) -> list[tuple[str, int]]:
    """Capitalised name-like tokens with start offsets; trailing 's stripped."""
    out = []
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        out.append((tok[:-2] if tok.endswith("'s") else tok, m.start()))
    return out


def _is_sentence_initial(text: str, start: int) -> bool:
    prefix = text[:start].rstrip(" \t\"'(")
    if not prefix:
        return True
    if prefix[-1] in _INITIAL_AFTER:
        return True
    return prefix[-1] == "-" and (len(prefix) == 1 or prefix[-2].isspace())


def _non_latin_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    non_latin = sum(1 for c in letters if not unicodedata.name(c, "").startswith("LATIN"))
    return non_latin / len(letters)


@cache
def _all_name_pool_names() -> frozenset[str]:
    names: set[str] = set()
    for sid in list_available_settings():
        for archetype in load_setting(sid).advisor_archetypes:
            names.update(archetype.name_pool)
    return frozenset(names)


@cache
def _foreign_resource_words(setting_id: str) -> frozenset[str]:
    own = {w for r in load_setting(setting_id).resources for w in (r.id, r.name.lower())}
    every = {
        w
        for sid in list_available_settings()
        for r in load_setting(sid).resources
        for w in (r.id, r.name.lower())
    }
    return frozenset(every - own)


def _term_tokens(texts: Iterable[str]) -> frozenset[str]:
    out: set[str] = set()
    for raw in texts:
        text = _normalize(raw)
        out.update(tok for tok, _ in _tokens(text))
        out.update(w for w in re.split(r"\s+", text) if w)
    return frozenset(out)


def validate_narration(
    text: str, allowed_terms: frozenset[str], roster_names: frozenset[str]
) -> list[Violation]:
    """Every rule violated by `text`, in a stable order (spec §4.2 table)."""
    norm = _normalize(text)
    if not norm.strip():
        return [Violation("empty")]
    found: list[Violation] = []
    if _MARKUP.search(norm):
        found.append(Violation("markup"))
    if _FOURTH_WALL.search(norm):
        found.append(Violation("fourth_wall"))
    for line in norm.splitlines():
        low = line.strip().lower()
        if low.startswith(_ECHO_PREFIXES) or "narrator-voice" in low:
            found.append(Violation("prompt_echo"))
            break
    if _non_latin_share(norm) > MAX_NON_LATIN_SHARE:
        found.append(Violation("script"))
    sentences = [s for s in _SENTENCE_SPLIT.split(norm.strip()) if s]
    if len(sentences) > MAX_SENTENCES or len(norm) > MAX_CHARS:
        found.append(Violation("too_long"))

    pools = _all_name_pool_names()
    seen: set[str] = set()
    for tok, start in _tokens(norm):
        if tok in allowed_terms or tok in _STOP or tok in seen:
            continue
        foreign_name = tok in pools and tok not in roster_names
        if _is_sentence_initial(norm, start) and not foreign_name:
            continue
        seen.add(tok)
        found.append(Violation(f"unknown_name:{tok}"))
    return found


def build_allowed_terms(
    setting: Setting, state: ColonyState, extra_texts: Iterable[str] = ()
) -> frozenset[str]:
    """Names the player has seen this run, plus the setting's own vocabulary."""
    texts: list[str] = [setting.player_title, setting.name, *setting.lexicon]
    texts += [a.name for a in state.agents] + [a.role for a in state.agents]
    for resolved in state.event_log:
        texts += [resolved.event.title, resolved.event.body, resolved.summary]
    if state.current_event is not None:
        texts += [state.current_event.title, state.current_event.body]
    texts += [t for p in state.turn_proposals for t in (p.name, p.description)]
    texts += list(extra_texts)
    return _term_tokens(texts)


def build_strict_terms(setting: Setting, state: ColonyState) -> frozenset[str]:
    """Audit-only allowlist: roster, lexicon and authored setting text only."""
    texts: list[str] = [setting.player_title, setting.name, setting.lore, setting.narrative_voice]
    texts += list(setting.lexicon)
    texts += [a.name for a in state.agents] + [a.role for a in state.agents]
    for e in setting.event_categories:
        texts += [e.prompt_template, e.title or ""]
    return _term_tokens(texts)


def sanitize_risk_note(
    note: str | None,
    setting: Setting,
    allowed_terms: frozenset[str],
    roster_names: frozenset[str],
) -> str | None:
    """The note if it is short, qualitative and clean; otherwise None."""
    if note is None:
        return None
    note = note.strip()
    if not note or len(note) > RISK_NOTE_MAX:
        return None
    if re.search(r"\d", note) or _STAT_WORDS.search(note):
        return None
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", note)}
    if words & _foreign_resource_words(setting.id):
        return None
    blocking = {"markup", "fourth_wall", "script", "unknown_name"}
    if any(v.kind in blocking for v in validate_narration(note, allowed_terms, roster_names)):
        return None
    return note
