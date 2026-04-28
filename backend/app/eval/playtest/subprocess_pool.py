"""Concurrency + retry + degrade rules wrapping PersonaSession.

Per spec §10: every failure is data. Single failures degrade gracefully
(fallback to first proposal in displayed_order — preserves seed-based
reproducibility, unbiased toward any flavor because order is shuffled).
Patterns get logged in `harness_findings` for the aggregator.

Failure-type dispatch uses the typed-exception hierarchy from
persona_session.py (PersonaTimeout / PersonaSubprocessError /
PersonaParseError) — counters differentiate but retry/degrade behavior
is identical (retry once, then synthesize fallback).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.eval.playtest.persona_session import (
    PersonaParseError,
    PersonaSession,
    PersonaSubprocessError,
    PersonaTimeout,
)
from app.eval.playtest.schemas import (
    PersonaRunVerdict,
    PersonaTurnFlags,
    PersonaTurnOutput,
)

logger = logging.getLogger(__name__)


@dataclass
class HarnessFinding:
    """Per-invocation counters for non-graceful events. Mirrors spec §9
    `runs.json.harness_findings` shape."""

    malformed_json_count: int = 0
    invalid_pick_count: int = 0
    persona_refusals: list[dict[str, Any]] = field(default_factory=list)
    subprocess_hangs: int = 0
    subprocess_crashes: int = 0
    aborted_runs: list[dict[str, Any]] = field(default_factory=list)
    by_persona: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: {"malformed": 0, "invalid_pick": 0})
    )


@dataclass
class PoolPickResult:
    """One turn's pool output: either a real persona output or a fallback.

    `output` is the parsed PersonaTurnOutput on success or a synthesized
    fallback on degrade (with `fallback_used=True`).
    """

    output: PersonaTurnOutput
    fallback_used: bool

    @property
    def chosen_proposal_id(self) -> str:
        return self.output.chosen_proposal_id


def _synthesize_fallback(*, turn: int, displayed_order: list[str]) -> PersonaTurnOutput:
    """Fallback pick when the persona fails twice. Picks the first proposal
    in shuffled `displayed_order`. Reaction explicitly notes the failure."""
    return PersonaTurnOutput(
        turn=turn,
        displayed_order=displayed_order,
        chosen_proposal_id=displayed_order[0],
        shown_position_of_chosen=0,
        reaction="(harness fallback — persona failed to respond)",
        flags=PersonaTurnFlags(
            surprise=False,
            tone_break=False,
            advisor_oc=False,
            stakes_low=False,
        ),
        quotes={},
    )


class SubprocessPool:
    """Bounded-concurrency pool wrapping PersonaSession invocations.

    Concurrency is enforced by a `threading.Semaphore`. Cross-game
    parallelism happens at the orchestrator level — one PersonaSession
    per game runs sequentially per game; multiple games run in parallel
    up to `max_concurrency`.
    """

    def __init__(self, *, max_concurrency: int = 5) -> None:
        self.max_concurrency = max_concurrency
        self._sema = threading.Semaphore(max_concurrency)
        self.findings = HarnessFinding()

    def run_first_turn(
        self,
        *,
        session: PersonaSession,
        turn_input: dict[str, Any],
        displayed_order: list[str],
    ) -> PoolPickResult:
        return self._run_with_retry(
            kind="first_turn",
            session=session,
            turn_input=turn_input,
            displayed_order=displayed_order,
        )

    def run_subsequent_turn(
        self,
        *,
        session: PersonaSession,
        turn_input: dict[str, Any],
        displayed_order: list[str],
    ) -> PoolPickResult:
        return self._run_with_retry(
            kind="subsequent_turn",
            session=session,
            turn_input=turn_input,
            displayed_order=displayed_order,
        )

    def run_final_verdict(
        self, *, session: PersonaSession, post_mortem_json: dict[str, Any]
    ) -> PersonaRunVerdict | None:
        """End-of-run verdict. Single retry; on second failure returns None
        (the run's verdict is recorded as missing in runs.json)."""
        with self._sema:
            for attempt in (1, 2):
                try:
                    return session.final_verdict(post_mortem_json=post_mortem_json)
                except PersonaTimeout as exc:
                    logger.warning(
                        "final_verdict timeout (attempt %d/2) for %s: %s",
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.subprocess_hangs += 1
                except PersonaSubprocessError as exc:
                    logger.warning(
                        "final_verdict subprocess crash (attempt %d/2) for %s: %s",
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.subprocess_crashes += 1
                except PersonaParseError as exc:
                    logger.warning(
                        "final_verdict parse error (attempt %d/2) for %s: %s",
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.malformed_json_count += 1
                    self.findings.by_persona[session.persona.id]["malformed"] += 1
                if attempt == 2:
                    return None
        return None

    def _run_with_retry(
        self,
        *,
        kind: str,
        session: PersonaSession,
        turn_input: dict[str, Any],
        displayed_order: list[str],
    ) -> PoolPickResult:
        with self._sema:
            method = session.first_turn if kind == "first_turn" else session.subsequent_turn
            for attempt in (1, 2):
                try:
                    out = method(turn_input=turn_input, displayed_order=displayed_order)
                except PersonaTimeout as exc:
                    logger.warning(
                        "%s timeout (attempt %d/2) for %s: %s",
                        kind,
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.subprocess_hangs += 1
                    continue
                except PersonaSubprocessError as exc:
                    logger.warning(
                        "%s subprocess crash (attempt %d/2) for %s: %s",
                        kind,
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.subprocess_crashes += 1
                    continue
                except PersonaParseError as exc:
                    logger.warning(
                        "%s parse error (attempt %d/2) for %s: %s",
                        kind,
                        attempt,
                        session.persona.id,
                        exc,
                    )
                    self.findings.malformed_json_count += 1
                    self.findings.by_persona[session.persona.id]["malformed"] += 1
                    continue

                # Defensive belt-and-suspenders. Pydantic schema validation
                # already enforces this; recorded as invalid_pick — distinct
                # from malformed_json per spec §10's failure table.
                if out.chosen_proposal_id not in displayed_order:
                    logger.warning(
                        "%s invalid pick (attempt %d/2) for %s: %r not in %r",
                        kind,
                        attempt,
                        session.persona.id,
                        out.chosen_proposal_id,
                        displayed_order,
                    )
                    self.findings.invalid_pick_count += 1
                    self.findings.by_persona[session.persona.id]["invalid_pick"] += 1
                    continue

                return PoolPickResult(output=out, fallback_used=False)

            return PoolPickResult(
                output=_synthesize_fallback(
                    turn=turn_input["turn"], displayed_order=displayed_order
                ),
                fallback_used=True,
            )
