"""Exit-gate audit for spec 1, truth & integrity (spec §8).

Reads game_sessions + turn_logs for the selected games from the LOCAL
database and prints one table. Exit code 1 if any gated check fails.

    cd backend && DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:54322/postgres \\
      uv run python -m scripts.audit_integrity --since 2026-10-20T00:00:00+00:00
"""

from __future__ import annotations

import argparse
import asyncio
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from app.agent.narration_guard import (
    HARD_CODES,
    build_allowed_terms,
    build_strict_terms,
    validate_narration,
)
from app.config import settings
from app.database import async_session_factory
from app.eval.tool_validity import (
    TOOL_VALIDITY_THRESHOLD,
    tool_validity_by_phase,
    tool_validity_rate,
)
from app.game.events import _RESPITE_TEMPLATES, _derive_title
from app.game.models import GameSession, TurnLog
from app.game.post_mortem import build_post_mortem
from app.game.preview import outcome_from_calls, render_effects, state_from_compact
from app.game.schemas import Event, ResolvedEvent, ToolCall
from app.game.setting_loader import Setting, list_available_settings, load_setting
from scripts._harness import require_local_database

COLD_WORDS = ("frost", "ice", "snow", "blizzard", "frozen", "sub-zero")
COLD_BASELINE = {"desert_outpost_v1": 23, "orbital_colony_v1": 12}
_COLD = re.compile(r"\b(" + "|".join(COLD_WORDS) + r")\b", re.IGNORECASE)
_SIGNED_THEN_WORD = re.compile(r"[+\-−]\s?\d+\s*%?\s*([A-Za-z]+)")
_WORD_THEN_SIGNED = re.compile(r"([A-Za-z]+)\s*[+\-−]\s?\d+")
_QUOTED = re.compile(r"\"([^\"]{2,})\"")
_AFTERMATH = "Aftermath of "
_SOMETIMES = re.compile(r"^sometimes\s+", re.IGNORECASE)
_STRAIGHTEN = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


@dataclass
class Tally:
    turns: int = 0
    skipped: int = 0
    preview_mismatch: int = 0
    mismatch_examples: list[str] = field(default_factory=list)
    picked_rejected: int = 0
    debate_stat_changes: int = 0
    stored_hard: int = 0
    first_hard: int = 0
    stored_unknown: int = 0
    prod_unknown: int = 0
    strict_unknown: int = 0
    fallbacks: int = 0
    titles: int = 0
    titles_bad: list[str] = field(default_factory=list)
    cold_words: Counter[str] = field(default_factory=Counter)
    tic_lines: Counter[tuple[str, str]] = field(default_factory=Counter)
    tic_hits: Counter[tuple[str, str]] = field(default_factory=Counter)
    tic_no_key: set[tuple[str, str]] = field(default_factory=set)
    tic_copies: int = 0
    tic_copy_examples: list[str] = field(default_factory=list)
    classes: Counter[str] = field(default_factory=Counter)
    numbers_in_text: int = 0
    risk_dropped: int = 0
    retried: int = 0
    calls: list[ToolCall] = field(default_factory=list)


def _setting_titles(setting: Setting) -> set[str]:
    """Authored titles, plus the derived fallback for categories without one."""
    return {e.title or _derive_title(e.prompt_template) for e in setting.event_categories}


@cache
def _shipped_titles() -> frozenset[str]:
    """Every title the engine may display: authored, derived, or respite."""
    out = {title for title, _body in _RESPITE_TEMPLATES}
    for setting_id in list_available_settings():
        out |= _setting_titles(load_setting(setting_id))
    return frozenset(out)


def title_ok(title: str, setting: Setting | None = None) -> bool:
    """A displayed title is from an allowed source: never just a short string.

    Allowed: authored `EventCategory.title`, `_derive_title` of a category
    without one, or a respite template title, across all shipped settings
    (plus `setting`, when given), optionally prefixed with "Aftermath of ".
    """
    core = title[len(_AFTERMATH) :] if title.startswith(_AFTERMATH) else title
    if not core:
        return False
    return core in _shipped_titles() or (setting is not None and core in _setting_titles(setting))


def tic_phrase(tic: str) -> str | None:
    m = _QUOTED.search(tic)
    return m.group(1).rstrip(" —-–") if m else None


def straighten_quotes(text: str) -> str:
    """Curly quotes and apostrophes to straight ones, so a model's typography can't dodge a match."""
    return text.translate(_STRAIGHTEN)


def tic_copy_needles(tic: str) -> list[str]:
    """Literal fragments of a tic that must never show up in an advisor line.

    The tic sentence with its leading "Sometimes" removed, its leading clause
    (before an em dash or semicolon), the quoted key phrase exactly as quoted
    in the tic (dash included: `"the probabilities suggest—"`), and that
    phrase in its dash-less quoted form. The bare key phrase is the tic working
    as intended, so it is not a copy; it is measured by the share row instead.
    All needles use straight quotes; match against `straighten_quotes(line)`.
    """
    tic = straighten_quotes(tic)
    text = _SOMETIMES.sub("", tic.strip()).rstrip(".").strip().lower()
    needles = [text] if text else []
    clause = re.split(r"\s+[—–-]\s+|;", text)[0].strip()
    if len(clause.split()) >= 3 and clause not in needles:
        needles.append(clause)
    quoted = _QUOTED.search(tic)
    if quoted:
        needles.append(f'"{quoted.group(1).lower()}"')
    phrase = tic_phrase(tic)
    if phrase and f'"{phrase.lower()}"' not in needles:
        needles.append(f'"{phrase.lower()}"')
    return needles


def numbers_near_stats(text: str, setting: Setting) -> bool:
    words = {"legitimacy", "health", "grievance"}
    words |= {r.id for r in setting.resources} | {r.name.lower() for r in setting.resources}
    hits = [m.group(1).lower() for m in _SIGNED_THEN_WORD.finditer(text)]
    hits += [m.group(1).lower() for m in _WORD_THEN_SIGNED.finditer(text)]
    return any(h in words for h in hits)


def _event_or_none(raw: dict[str, Any] | None) -> Event | None:
    """Parse a stored event; a malformed one (e.g. no title) must not crash the audit."""
    try:
        return Event.model_validate(raw) if raw else None
    except ValidationError:
        return None


def audit_turn(row: Any, setting: Setting, prior: list[ResolvedEvent], t: Tally) -> None:
    """Fold one turn_logs row into the tally.

    Rows without the branch's `proposal_classes` predate it (a game paused
    before the branch landed gets a meta dict holding only `narration`), so
    they would be audited against untrimmed calls and are skipped.
    """
    meta = row.turn_meta
    if not meta or "proposal_classes" not in meta or row.tool_calls is None:
        t.skipped += 1
        return
    t.turns += 1
    calls = [ToolCall.model_validate(c) for c in row.tool_calls]
    t.calls += calls
    t.retried += bool(meta.get("debate_retried"))
    t.classes.update(meta.get("proposal_classes") or [])
    t.risk_dropped += int(meta.get("risk_notes_dropped") or 0)
    t.debate_stat_changes += sum(
        1
        for c in calls
        if c.phase == "debate" and c.rejection_reason is None and c.name != "propose_action"
    )
    resolve = [c for c in calls if c.phase == "resolve"]
    t.picked_rejected += sum(1 for c in resolve if c.rejection_reason)

    pre = state_from_compact(meta["pre_pick"]) if meta.get("pre_pick") else None
    chosen_id = (row.chosen or {}).get("chosen_proposal_id")
    picked = next((p for p in row.proposals or [] if p.get("id") == chosen_id), None)
    if picked is not None and pre is not None:
        rendered = render_effects(setting, outcome_from_calls(resolve, pre))
        if rendered != list(picked.get("effects_preview") or []):
            t.preview_mismatch += 1
            t.mismatch_examples.append(
                f"{row.game_id} t{row.turn_number}: {picked.get('effects_preview')} vs {rendered}"
            )
    for p in row.proposals or []:
        if numbers_near_stats(f"{p.get('name', '')} {p.get('description', '')}", setting):
            t.numbers_in_text += 1

    title = str((row.event or {}).get("title", ""))
    t.titles += 1
    if not title_ok(title, setting):
        t.titles_bad.append(title)

    narration_meta = meta.get("narration") or {}
    t.fallbacks += narration_meta.get("source") == "fallback"
    kept = narration_meta.get("kept_violations") or []
    t.prod_unknown += any(str(code).startswith("unknown_name") for code in kept)
    first = narration_meta.get("first_attempt_violations") or []
    t.first_hard += any(code.split(":", 1)[0] in HARD_CODES for code in first)

    if row.narration and pre is not None:
        event = _event_or_none(row.event)
        state = pre.model_copy(update={"event_log": prior, "current_event": event})
        extra = [str(r.get("text", "")) for r in row.agent_responses or []]
        for p in row.proposals or []:
            # Mirrors nodes.py: each surfaced proposal's text and effects_preview lines.
            extra += [str(p.get("name", "")), str(p.get("description", ""))]
            extra += [str(line) for line in p.get("effects_preview") or []]
        roster = frozenset(a.name for a in pre.agents)
        found = validate_narration(
            row.narration, build_allowed_terms(setting, state, extra), roster
        )
        t.stored_hard += any(v.hard for v in found)
        t.stored_unknown += any(v.kind == "unknown_name" for v in found)
        strict = validate_narration(row.narration, build_strict_terms(setting, pre), roster)
        t.strict_unknown += any(v.kind == "unknown_name" for v in strict)
        if setting.id in COLD_BASELINE:
            t.cold_words[setting.id] += len(_COLD.findall(row.narration))

    if pre is not None:
        role_by_agent = {str(a.id): a.role for a in pre.agents}
        for r in row.agent_responses or []:
            role = role_by_agent.get(str(r.get("agent_id")))
            arch = next((a for a in setting.advisor_archetypes if a.role_name == role), None)
            if arch is None or role is None:
                continue
            key = (setting.id, role)
            line = straighten_quotes(str(r.get("text", ""))).lower()
            t.tic_lines[key] += 1
            if any(n in line for n in tic_copy_needles(arch.verbal_tic)):
                t.tic_copies += 1
                t.tic_copy_examples.append(f"{key[0]} {role}: {str(r.get('text', ''))[:100]!r}")
            phrase = tic_phrase(arch.verbal_tic)
            if phrase:
                t.tic_hits[key] += phrase.lower() in line
            else:
                t.tic_no_key.add(key)


def _share(n: int, d: int) -> float:
    return n / d if d else 0.0


def build_report(
    t: Tally, *, dormant_failures: int, costs: list[int]
) -> list[tuple[str, str, str, str]]:
    """Rows of (check, value, target, PASS|FAIL|INFO)."""
    rows: list[tuple[str, str, str, str]] = []

    def gate(name: str, value: float, target: str, ok: bool, fmt: str = "{:.0f}") -> None:
        rows.append((name, fmt.format(value), target, "PASS" if ok else "FAIL"))

    def info(name: str, value: str, target: str = "reported") -> None:
        rows.append((name, value, target, "INFO"))

    n = t.turns
    gate("Turns audited", n, "> 0", n > 0)
    gate("Preview ≠ resolve render", t.preview_mismatch, "0", t.preview_mismatch == 0)
    gate("Picked calls rejected at resolve", t.picked_rejected, "0", t.picked_rejected == 0)
    gate("Stat changes applied in debate", t.debate_stat_changes, "0", t.debate_stat_changes == 0)
    gate("Stored narrations with a hard violation", t.stored_hard, "0", t.stored_hard == 0)
    gate(
        "First attempts with a hard violation",
        _share(t.first_hard, n),
        "≤ 5%",
        _share(t.first_hard, n) <= 0.05,
        "{:.1%}",
    )
    gate(
        "Stored narrations with unknown_name",
        _share(t.stored_unknown, n),
        "≤ 2%",
        _share(t.stored_unknown, n) <= 0.02,
        "{:.1%}",
    )
    info("Stored narrations, strict unknown_name", f"{_share(t.strict_unknown, n):.1%}")
    info(
        "Production-recorded unknown_name (kept)",
        f"{_share(t.prod_unknown, n):.1%} ({t.prod_unknown} turns)",
    )
    gate(
        "Narration fallbacks",
        _share(t.fallbacks, n),
        "≤ 5%",
        _share(t.fallbacks, n) <= 0.05,
        "{:.1%}",
    )
    gate(
        "Event titles outside the allowed sources",
        len(t.titles_bad),
        "0",
        not t.titles_bad,
    )
    for sid, base in COLD_BASELINE.items():
        count = t.cold_words[sid]
        rows.append(
            (
                f"Cold words in {sid} narration",
                f"{count} (baseline {base})",
                f"< baseline {base}",
                "WARN" if count >= base else "INFO",
            )
        )
    gate("Literal tic text copied", t.tic_copies, "0", t.tic_copies == 0)
    for (sid, role), lines in sorted(t.tic_lines.items()):
        if (sid, role) in t.tic_no_key:
            info(f"Tic phrase share, {sid} {role}", f"no key phrase ({lines} lines)")
        else:
            info(f"Tic phrase share, {sid} {role}", f"{_share(t.tic_hits[(sid, role)], lines):.0%}")
    gate(
        "Games with dormant seeds but empty post-mortem",
        dormant_failures,
        "0",
        dormant_failures == 0,
    )
    rate = tool_validity_rate(t.calls)
    gate(
        "tool_validity_rate (LLM phases)",
        rate,
        f"≥ {TOOL_VALIDITY_THRESHOLD:.2f}",
        rate >= TOOL_VALIDITY_THRESHOLD,
        "{:.3f}",
    )
    info("Validity by phase", str(tool_validity_by_phase(t.calls)))
    surfaced = sum(t.classes.values())
    info(
        "Surfaced degraded / no_effect",
        f"{_share(t.classes['degraded'], surfaced):.1%} / "
        f"{_share(t.classes['no_effect'], surfaced):.1%}",
    )
    info("Proposals with numbers next to stat words", str(t.numbers_in_text))
    info("risk_note drops", str(t.risk_dropped))
    gate(
        "Debate retry rate",
        _share(t.retried, n),
        "≤ 25%",
        _share(t.retried, n) <= 0.25,
        "{:.1%}",
    )
    p95 = statistics.quantiles(costs, n=20)[18] if len(costs) >= 2 else float(max(costs, default=0))
    gate("Cost per game p95 (cents)", p95, "≤ 25", p95 <= 25, "{:.1f}")
    return rows


async def _run(game_ids: list[UUID] | None, since: datetime | None) -> int:
    t = Tally()
    dormant_failures = 0
    costs: list[int] = []
    async with async_session_factory() as db:
        query = select(GameSession)
        if game_ids:
            query = query.where(GameSession.id.in_(game_ids))
        if since is not None:
            query = query.where(GameSession.started_at >= since)
        games = (await db.execute(query)).scalars().all()
        per_game: list[str] = []
        for game in games:
            setting = load_setting(game.setting_id)
            logs = (
                (
                    await db.execute(
                        select(TurnLog)
                        .where(TurnLog.game_id == game.id)
                        .order_by(TurnLog.turn_number)
                    )
                )
                .scalars()
                .all()
            )
            prior: list[ResolvedEvent] = []
            audited_before = t.turns
            for row in logs:
                audit_turn(row, setting, list(prior), t)
                if row.event:
                    chosen = str((row.chosen or {}).get("chosen_proposal_id") or "")
                    picked = next((p for p in row.proposals or [] if p.get("id") == chosen), {})
                    event = _event_or_none(row.event)
                    if event is not None:
                        prior.append(
                            ResolvedEvent(
                                turn_number=row.turn_number,
                                event=event,
                                chosen_proposal_id=chosen,
                                summary=str(picked.get("description", "")),
                            )
                        )
            per_game.append(
                f"  {game.id} {game.setting_id}: {t.turns - audited_before}/{len(logs)} "
                "turns audited"
            )
            if game.end_state is not None:
                costs.append(int(game.cost_cents))
                has_dormant = any(
                    s.get("dormant") for s in (game.state or {}).get("consequence_seeds") or []
                )
                if has_dormant:
                    pm = await build_post_mortem(game_id=game.id, db=db)
                    dormant_failures += not pm.counterfactuals.dormant_seeds

    rows = build_report(t, dormant_failures=dormant_failures, costs=costs)
    print(
        f"Audited {len(games)} games: {t.turns} turns audited, {t.skipped} skipped "
        "(no turn_meta from this branch).\n"
    )
    for line in per_game[:50]:
        print(line)
    if per_game:
        print()
    width = max(len(r[0]) for r in rows)
    for check, value, target, status in rows:
        print(f"{status:<5} {check:<{width}}  {value:>12}  target {target}")
    for example in t.mismatch_examples[:10]:
        print(f"  mismatch: {example}")
    for example in t.tic_copy_examples[:10]:
        print(f"  tic copy: {example}")
    for title in t.titles_bad[:10]:
        print(f"  bad title: {title!r}")
    return 1 if any(r[3] == "FAIL" for r in rows) else 0


def main() -> None:  # pragma: no cover — CLI entry
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--since", type=datetime.fromisoformat, help="ISO 8601 start time")
    group.add_argument("--game-ids", help="Comma-separated game ids")
    args = parser.parse_args()
    require_local_database(settings.DATABASE_URL)
    ids = [UUID(x) for x in args.game_ids.split(",")] if args.game_ids else None
    sys.exit(asyncio.run(_run(ids, args.since)))


if __name__ == "__main__":  # pragma: no cover
    main()
