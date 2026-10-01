"""Procedural run generator — builds a fresh ColonyState from a setting + seed.

Deterministic given `(user_id, setting_id, seed)`:
  - Same inputs → identical agents (names, UUIDs, axes, portrait seeds).
  - Different seeds → different agents (names may rarely collide; axes almost surely differ).
  - Rolled content (max_turns, advisor names/axes, portrait seeds) depends on
    `(setting_id, seed)` only, so two users given the same seed play the same
    colony. Identity (game_id, agent ids) folds in user_id / game_id so rows
    never collide. The playtest harness and balance sweep rely on this for
    paired comparisons.
  - Stable across processes: the RNG seed uses sha256, never `hash(str)`
    (salted per process by PYTHONHASHSEED).

No I/O beyond the setting YAML load, no LLM calls, no mutation of global state.
All randomness routes through a single `random.Random` seeded from the inputs.

See SPEC §6.1 (axes), §6.2 (archetype grid), §6.3 (agent state), §7.1 (pacing).
"""

from __future__ import annotations

import hashlib
import random
from uuid import NAMESPACE_URL, UUID, uuid5

from app.game.schemas import Agent, ColonyState, Quadrant, Resource
from app.game.setting_loader import AdvisorArchetype, Setting, load_setting

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Per SPEC §7.1: daily runs are short. Turn count is random in [8, 12].
MIN_TURNS = 8
MAX_TURNS = 12

# Per SPEC §6.2: the four archetype quadrants have natural ethics/temperament poles.
# ethics:      - = pragmatist, + = idealist
# temperament: - = cautious,   + = bold
# loyalty is rolled independently per advisor.
_QUADRANT_AXIS_BIAS: dict[Quadrant, dict[str, int]] = {
    Quadrant.ACCOUNTANT: {"ethics": -1, "temperament": -1},  # pragmatist + cautious
    Quadrant.OPERATOR: {"ethics": -1, "temperament": +1},  # pragmatist + bold
    Quadrant.CONSCIENCE: {"ethics": +1, "temperament": -1},  # idealist + cautious
    Quadrant.CRUSADER: {"ethics": +1, "temperament": +1},  # idealist + bold
}

# UUID namespace for agent.id derivation. NAMESPACE_URL chosen because the
# portrait_seed is a URL-ish stable string identifier; the choice is arbitrary
# but fixed forever — changing it would break persisted run identity.
_AGENT_UUID_NAMESPACE = NAMESPACE_URL


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def _seed_rng(setting_id: str, seed: int) -> random.Random:
    """Build a deterministic content RNG from `(setting_id, seed)`.

    sha256 instead of `hash(setting_id)`: str hashing is salted per process,
    which made the same seed roll different colonies in different runs.
    user_id is deliberately left out so a seed means the same colony for
    every player (see module docstring).
    """
    digest = hashlib.sha256(f"{setting_id}:{seed}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _biased_roll(rng: random.Random, direction: int) -> float:
    """Roll one axis value.

    direction == +1 → always on the positive pole, value in [+0.5, +1.0].
    direction == -1 → always on the negative pole, value in [-1.0, -0.5].
    direction ==  0 → independent, uniform in [-1.0, +1.0].
    """
    if direction == 0:
        return rng.uniform(-1.0, 1.0)
    return direction * (0.5 + rng.random() * 0.5)


def _build_agent(
    *,
    game_id: UUID,
    setting_id: str,
    archetype: AdvisorArchetype,
    rng: random.Random,
) -> Agent:
    """Roll a single advisor from the given archetype template."""
    name = rng.choice(archetype.name_pool)
    bias = _QUADRANT_AXIS_BIAS[archetype.quadrant]
    ethics = _biased_roll(rng, bias["ethics"])
    temperament = _biased_roll(rng, bias["temperament"])
    loyalty = _biased_roll(rng, 0)

    # Deterministic portrait seed: encodes every input a future restore needs.
    portrait_seed = f"{setting_id}:{archetype.role_name}:{name}:{rng.getrandbits(16):04x}"
    # game_id in the id input: content is user-independent, so two games on
    # the same seed roll the same portrait_seed and would otherwise collide
    # on agents.id.
    agent_id = uuid5(_AGENT_UUID_NAMESPACE, f"{game_id}:{portrait_seed}")

    return Agent(
        id=agent_id,
        game_id=game_id,
        name=name,
        role=archetype.role_name,
        archetype_quadrant=archetype.quadrant,
        ethics=ethics,
        temperament=temperament,
        loyalty=loyalty,
        portrait_seed=portrait_seed,
        health=100,
        morale=80,
        grievance=0,
        is_alive=True,
        memory=[],
        relationships={},
        transformation=None,
        verbal_tic=archetype.verbal_tic,
    )


def _build_resources(setting: Setting) -> dict[str, Resource]:
    """Instantiate each setting ResourceDef at its starting value."""
    return {
        r.id: Resource(
            id=r.id,
            name=r.name,
            icon=r.icon,
            value=r.starting,
            max=r.max,
            critical_threshold=r.critical_threshold,
        )
        for r in setting.resources
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_run(
    *,
    user_id: UUID,
    setting_id: str,
    seed: int,
    run_key: str | None = None,
) -> ColonyState:
    """Build a fresh ColonyState for a new run. Deterministic given inputs.

    Steps:
      1. Load the setting YAML (load_setting).
      2. Seed a random.Random from (setting_id, seed).
      3. Pick max_turns uniformly from [MIN_TURNS, MAX_TURNS].
      4. Build one agent per archetype (setting YAML is pre-validated to cover
         all four quadrants, see Setting._archetypes_cover_all_quadrants).
      5. Build the resource dict from setting.resources.
      6. Assemble a fresh ColonyState at turn 0 with no seeds/events/proposals.
    """
    setting = load_setting(setting_id)
    rng = _seed_rng(setting_id, seed)

    max_turns = rng.randint(MIN_TURNS, MAX_TURNS)
    # Derive game_id deterministically from inputs so three calls with the same
    # (user_id, setting_id, seed) produce byte-identical state dumps.
    # `run_key` (the service passes a fresh one per created run) keeps
    # game_id unique when the same user replays a seed; game_id doubles as
    # the LangGraph thread_id, so a reused id would resume a stale
    # checkpoint. Content does not depend on it.
    identity = f"game:{user_id}:{setting_id}:{seed}" + (f":{run_key}" if run_key else "")
    game_id = uuid5(_AGENT_UUID_NAMESPACE, identity)

    agents = [
        _build_agent(
            game_id=game_id,
            setting_id=setting_id,
            archetype=archetype,
            rng=rng,
        )
        for archetype in setting.advisor_archetypes
    ]

    resources = _build_resources(setting)

    return ColonyState(
        game_id=game_id,
        user_id=user_id,
        setting_id=setting_id,
        seed=seed,
        turn_number=0,
        max_turns=max_turns,
        resources=resources,
        agents=agents,
        legitimacy=100,
        player_health=100,
        consequence_seeds=[],
        event_log=[],
        current_event=None,
        turn_proposals=[],
        turn_tool_calls=[],
        turn_cost_cents=0,
        end_state=None,
    )
