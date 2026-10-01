"""LLMPlayer — drop-in for SimulatedPlayer in run_simulator.simulate_game.

Interface contract: `pick(proposals: list[dict]) -> str` (proposal id).
Internally, the player shuffles proposals, asks the pool to dispatch the
turn (first or subsequent), records the output, and returns the chosen id.

State is per-game: one LLMPlayer instance backs one game. The orchestrator
constructs one per simulated game and passes it into simulate_game.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.eval.playtest.persona_session import DEFAULT_PERSONA_MODEL, PersonaSession
from app.eval.playtest.schemas import Persona, PersonaTurnOutput
from app.eval.playtest.subprocess_pool import PoolPickResult, SubprocessPool


class LLMPlayer:
    """Per-game LLM player. Conforms to the SimulatedPlayer interface
    (`pick(proposals) -> id`) so it can drop into run_simulator.simulate_game
    without changes."""

    def __init__(
        self,
        *,
        persona: Persona,
        pool: SubprocessPool,
        shell_dir: Path,
        seed: int,
        turn_input_provider: Callable[[], dict[str, Any]],
        effort: str = "high",
        per_turn_timeout_seconds: int = 300,
        model: str = DEFAULT_PERSONA_MODEL,
    ) -> None:
        self.persona = persona
        self.pool = pool
        self.shell_dir = shell_dir
        self.seed = seed
        self.turn_input_provider = turn_input_provider
        self._rng = random.Random(seed)
        self._session = PersonaSession(
            persona=persona,
            shell_dir=shell_dir,
            effort=effort,  # type: ignore[arg-type]
            per_turn_timeout_seconds=per_turn_timeout_seconds,
            model=model,
        )
        self._first_turn_done = False
        self.per_turn_outputs: list[PersonaTurnOutput] = []
        # The UI-parity view shown each turn, kept for the archive's turn context.
        self.per_turn_inputs: list[dict[str, Any]] = []
        self.fallback_turns: list[int] = []  # turn numbers where pool fell back

    def pick(self, proposals: list[Any]) -> str:
        """The SimulatedPlayer-compatible interface called by simulate_game."""
        displayed_order = self._displayed_order_for(proposals)

        turn_input = self.turn_input_provider()
        self.per_turn_inputs.append(turn_input)
        if not self._first_turn_done:
            result = self.pool.run_first_turn(
                session=self._session,
                turn_input=turn_input,
                displayed_order=displayed_order,
            )
            self._first_turn_done = True
        else:
            result = self.pool.run_subsequent_turn(
                session=self._session,
                turn_input=turn_input,
                displayed_order=displayed_order,
            )

        self._record_output(result)
        return result.chosen_proposal_id

    def _displayed_order_for(self, proposals: list[Any]) -> list[str]:
        """Per-turn shuffle of proposal ids — deterministic per `seed`.

        Per spec §5: each turn we randomly shuffle `[DOMINANT, MORAL, WILDCARD]`
        in display order so per-persona pick rates can be checked against
        position correlation (Spearman ρ > 0.2 marks position-bias contamination).
        """
        ids = [p["id"] if isinstance(p, dict) else str(p.id) for p in proposals]
        shuffled = ids[:]
        self._rng.shuffle(shuffled)
        return shuffled

    def _record_output(self, result: PoolPickResult) -> None:
        self.per_turn_outputs.append(result.output)
        if result.fallback_used:
            self.fallback_turns.append(result.output.turn)
