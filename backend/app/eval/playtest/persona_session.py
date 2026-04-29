"""Single-game persona subprocess wrapper.

Wraps `claude --print` (first turn) and `claude --resume <session-id> --print`
(subsequent turns) for one game's persona. Captures the session ID returned
in the JSON envelope and reuses it across the run's turns.

Per spec §11: cwd is a clean shell directory (no project CLAUDE.md autoload),
output format is JSON envelope, persona instructions injected via
--append-system-prompt, model is sonnet-4-6, effort is `high` for personas
and `max` for the meta-aggregator (the aggregator does NOT use this class —
it runs as an Agent subagent inside the user's Claude Code session).

Failure handling (spec §10) lives in subprocess_pool.py — this class returns
parsed output on success and raises on hard failure (subprocess exit non-zero,
JSON parse failure, schema validation failure). The pool catches and retries.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from app.eval.playtest.schemas import (
    Persona,
    PersonaRunVerdict,
    PersonaTurnFlags,
    PersonaTurnOutput,
)

logger = logging.getLogger(__name__)

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]
SONNET_MODEL_ID = "claude-sonnet-4-6"


class PersonaSessionError(RuntimeError):
    """Base exception for PersonaSession failures. SubprocessPool catches
    this base and dispatches on subclass for spec §10 retry/degrade rules."""


class PersonaTimeout(PersonaSessionError):  # noqa: N818 — name is part of the public API
    """Subprocess hit the per-turn timeout (subprocess.TimeoutExpired). Pool
    should retry once via --resume, then degrade to fallback pick."""


class PersonaSubprocessError(PersonaSessionError):
    """Subprocess returned non-zero exit code. Pool should retry once via
    --resume, then degrade."""


class PersonaParseError(PersonaSessionError):
    """Output failed JSON parse OR Pydantic schema validation. Pool should
    re-prompt once with strict schema reminder, then degrade."""


def _build_system_prompt(persona: Persona) -> str:
    """The 3-section persona prompt. Frame + persona block + output contract.

    Frame and output contract are identical across all 7 personas; only the
    persona block varies. Uniformity is load-bearing for cross-persona
    comparability (spec §4).
    """
    frame = (
        "You are play-testing Outpost, a daily browser narrative roguelike. "
        "Four AI advisors run a fragile colony under your command. Permadeath. "
        "Each turn: an event, three proposals, advisor reactions, narration of "
        "the outcome. Runs end at turn 8-12 in one of: Endured, Collapsed, "
        "Ousted, Killed.\n\n"
        "You are reacting fresh — you have not seen this game before. You have "
        "no codebase context, no design documents, no prior plays. Stay in your "
        "persona's voice for in-character text; speak analytically for flags "
        "and scores. Reasoning before scoring (always).\n\n"
        "Reading discipline (load-bearing): the user message includes a "
        "`## Advisor reactions` section that lists every living advisor by "
        "name with their actual reaction text. Read it line-by-line before "
        "evaluating `advisor_oc` or claiming silence. An advisor counts as "
        "silent ONLY if the dashboard literally renders `(silent)` next to "
        "their name. Quotes you cite for any flag — especially `advisor_oc` "
        "— MUST be verbatim excerpts from the rendered dashboard text. "
        "Fabricating a `(silent)` claim against an advisor whose reaction is "
        "rendered, or paraphrasing reaction text into a quote, is a contract "
        "violation."
    )

    persona_block = (
        f"\n\nPicking rule: {persona.picking_rule}\n"
        f"Evaluation lens: {persona.evaluation_lens}\n"
        f"Voice traits: {persona.voice_traits}"
    )

    output_contract = (
        "\n\nPer turn, respond with ONLY a single JSON code-fence — no prose "
        "before or after. The JSON must validate against this shape:\n"
        "```json\n"
        "{\n"
        '  "turn": <int>,\n'
        '  "displayed_order": [<id1>, <id2>, <id3>],   // verbatim from input\n'
        '  "chosen_proposal_id": <one of displayed_order>,\n'
        '  "shown_position_of_chosen": <0, 1, or 2>,\n'
        '  "reaction": "<1-2 sentences in your voice>",\n'
        '  "flags": {\n'
        '    "surprise": <bool>, "tone_break": <bool>,\n'
        '    "advisor_oc": <bool>, "stakes_low": <bool>\n'
        "  },\n"
        '  "quotes": { "<flag_name>": "<≤30-word quote>", ... }  // only for True flags\n'
        "}\n```\n"
        "A bare True flag without a quote is a parse error. The harness will "
        "re-prompt once if your JSON is malformed."
    )

    return frame + persona_block + output_contract


def _parse_envelope_and_inner(raw_stdout: str) -> tuple[str, dict[str, Any]]:
    """Extract session_id and inner JSON from the `claude --output-format json`
    envelope. Returns (session_id, inner_dict). Raises PersonaParseError on
    any parse failure."""
    try:
        envelope = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        raise PersonaParseError(f"envelope is not JSON: {exc}") from exc

    session_id = envelope.get("session_id")
    if not session_id:
        raise PersonaParseError("envelope missing session_id")

    result_text = envelope.get("result", "")
    if not isinstance(result_text, str):
        raise PersonaParseError("envelope.result is not a string")

    # Strip optional ```json ... ``` fence.
    text = result_text.strip()
    if text.startswith("```"):
        # take content between first and last fence
        first_nl = text.find("\n")
        last_fence = text.rfind("```")
        if first_nl == -1 or last_fence <= first_nl:
            raise PersonaParseError("malformed code-fence in result")
        text = text[first_nl + 1 : last_fence].strip()

    try:
        inner = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PersonaParseError(f"inner result is not JSON: {exc}") from exc

    if not isinstance(inner, dict):
        raise PersonaParseError("inner result is not a JSON object")

    return session_id, inner


def _format_turn_user_message(*, turn_input: dict[str, Any], displayed_order: list[str]) -> str:
    """Renders the harness's per-turn user-message text shown to the persona.

    Section order: Setting → Dashboard → Event → Advisor reactions →
    Proposals → "Pick one". Reactions deliberately precede proposals: the
    earlier order (proposals first) caused Sonnet personas to skim past
    the reactions section and fabricate `(silent)` allegations against
    advisors whose reactions WERE rendered (24/0 false-positive ratio in
    the 2026-04-29 smoke run).

    Format is human-readable markdown so the LLM reads it naturally
    (research finding: markdown beats JSON for narrative pattern matching
    by LLMs).
    """
    proposals_by_id = {p["id"]: p for p in turn_input["proposals"]}
    advisor_by_id = {a["id"]: a for a in turn_input["dashboard"]["advisors"]}
    reactions_by_aid = {r["agent_id"]: r["text"] for r in turn_input["advisor_reactions"]}

    lines: list[str] = [
        f"# Turn {turn_input['turn']}",
        "",
        "## Setting",
        turn_input["setting_lore"],
        "",
        "## Dashboard",
        f"- Legitimacy: {turn_input['dashboard']['legitimacy']}",
        f"- Player health: {turn_input['dashboard']['player_health']}",
        "- Resources: "
        + ", ".join(f"{k} {v}" for k, v in turn_input["dashboard"]["resources"].items()),
        "",
        "## Event",
        f"**{turn_input['event']['title']}**",
        turn_input["event"]["body"],
        "",
        "## Advisor reactions",
    ]
    for a in turn_input["dashboard"]["advisors"]:
        text = reactions_by_aid.get(a["id"], "(silent)")
        lines.append(f"- **{a['name']}** ({a['role']}): {text}")
    lines.append("")
    lines.append("## Proposals (in display order)")
    for idx, pid in enumerate(displayed_order):
        p = proposals_by_id[pid]
        proposer = advisor_by_id.get(p.get("proposed_by_agent_id", ""))
        proposer_name = proposer["name"] if proposer else "(unknown)"
        lines.append(
            f"{idx}. **{p['name']}** ({p['flavor']}, by {proposer_name}) — {p['description']}"
        )
    lines.append("")
    lines.append(
        f"Pick one of the three proposals above. Your `displayed_order` field "
        f"in the response must be exactly: {displayed_order}"
    )
    return "\n".join(lines)


def _validate_quotes_against_dashboard(
    output: PersonaTurnOutput, user_message: str
) -> PersonaTurnOutput:
    """Drop fabricated `(silent)` quotes and downgrade their flags.

    A `(silent)` substring inside any `quotes[*]` value is legitimate ONLY
    when the rendered user message also contains `(silent)` markers (i.e.
    the dashboard literally rendered silence for at least one advisor).
    When every advisor's reaction is rendered, the dashboard contains zero
    `(silent)` markers — so a persona quote of `(silent)` is fabrication.

    On detection: drop the offending quote and set the corresponding flag
    to False. The persona's free-form `reaction` prose is left untouched
    (we can't audit free-form text, and the validator's job is to gate
    the binary flag, not rewrite voice).

    Conservative when the dashboard DOES render silence: we can't tell
    which advisors are legitimately silent vs fabricated, so we accept
    every `(silent)` quote in that case. The verbatim instruction in the
    system prompt carries the load there.
    """
    if "(silent)" in user_message:
        return output

    affected_flags = {k for k, v in output.quotes.items() if "(silent)" in v}
    if not affected_flags:
        return output

    for flag_name in sorted(affected_flags):
        logger.warning(
            "fabricated-silence guard: dropping %s flag (no '(silent)' marker "
            "in rendered dashboard); fabricated quote was %r",
            flag_name,
            output.quotes[flag_name],
        )

    new_quotes = {k: v for k, v in output.quotes.items() if k not in affected_flags}
    flags_dict = output.flags.model_dump()
    for k in affected_flags:
        if k in flags_dict:
            flags_dict[k] = False

    return output.model_copy(update={"quotes": new_quotes, "flags": PersonaTurnFlags(**flags_dict)})


class PersonaSession:
    """One persona session backing one game.

    Lifecycle:
      1. `first_turn(...)` — `claude --print` first invocation; captures session_id
      2. `subsequent_turn(...)` — `claude --resume <session_id> --print`
      3. `final_verdict(...)` — sends post-mortem JSON, expects PersonaRunVerdict

    Each method returns parsed output or raises PersonaSessionError. The
    SubprocessPool catches and retries per spec §10.
    """

    def __init__(
        self,
        *,
        persona: Persona,
        shell_dir: Path,
        effort: EffortLevel = "high",
        per_turn_timeout_seconds: int = 300,
        model: str = SONNET_MODEL_ID,
    ) -> None:
        self.persona = persona
        self.shell_dir = shell_dir
        self.effort = effort
        self.per_turn_timeout_seconds = per_turn_timeout_seconds
        self.model = model
        self.session_id: str | None = None

    def first_turn(
        self, *, turn_input: dict[str, Any], displayed_order: list[str]
    ) -> PersonaTurnOutput:
        """Invoke `claude --print` for the first turn. Captures session_id."""
        system_prompt = _build_system_prompt(self.persona)
        user_message = _format_turn_user_message(
            turn_input=turn_input, displayed_order=displayed_order
        )
        cmd = [
            "claude",
            "--print",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--append-system-prompt",
            system_prompt,
            user_message,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.shell_dir,
                capture_output=True,
                text=True,
                timeout=self.per_turn_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PersonaTimeout(f"first-turn timeout after {exc.timeout}s") from exc

        if proc.returncode != 0:
            raise PersonaSubprocessError(
                f"first-turn subprocess returned {proc.returncode}: {proc.stderr[:500]}"
            )

        session_id, inner = _parse_envelope_and_inner(proc.stdout)
        self.session_id = session_id

        try:
            output = PersonaTurnOutput.model_validate(inner)
        except ValidationError as exc:
            raise PersonaParseError(f"per-turn output failed validation: {exc}") from exc
        return _validate_quotes_against_dashboard(output, user_message)

    def subsequent_turn(
        self, *, turn_input: dict[str, Any], displayed_order: list[str]
    ) -> PersonaTurnOutput:
        """Continue an existing session via `claude --resume`."""
        if self.session_id is None:
            raise RuntimeError("no session_id — call first_turn() before subsequent_turn()")

        user_message = _format_turn_user_message(
            turn_input=turn_input, displayed_order=displayed_order
        )
        cmd = [
            "claude",
            "--resume",
            self.session_id,
            "--print",
            "--output-format",
            "json",
            "--effort",
            self.effort,
            user_message,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.shell_dir,
                capture_output=True,
                text=True,
                timeout=self.per_turn_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PersonaTimeout(f"subsequent-turn timeout after {exc.timeout}s") from exc

        if proc.returncode != 0:
            raise PersonaSubprocessError(
                f"subsequent-turn subprocess returned {proc.returncode}: {proc.stderr[:500]}"
            )

        # session_id from envelope should match self.session_id; we don't
        # re-assign because resume preserves it.
        _, inner = _parse_envelope_and_inner(proc.stdout)

        try:
            output = PersonaTurnOutput.model_validate(inner)
        except ValidationError as exc:
            raise PersonaParseError(f"per-turn output failed validation: {exc}") from exc
        return _validate_quotes_against_dashboard(output, user_message)

    def final_verdict(self, *, post_mortem_json: dict[str, Any]) -> PersonaRunVerdict:
        """Send the post-mortem JSON to the persona; expect a run-level verdict."""
        if self.session_id is None:
            raise RuntimeError("no session_id — call first_turn() first")

        user_message = (
            "# End of run\n\n"
            "Below is the full post-mortem (counterfactuals, dormant seeds, "
            "near-miss coups, end_state). Read it carefully, then emit ONLY a "
            "JSON code-fence with the run-level verdict.\n\n"
            "Schema:\n"
            "```json\n"
            "{\n"
            '  "axes": {\n'
            '    "stakes_felt_real":      {"score": 1-5, "rationale": "...", "turn_citations": [int, ...]},\n'
            '    "tonal_coherence":       {"score": 1-5, "rationale": "...", "turn_citations": []},\n'
            '    "advisor_distinctness":  {"score": 1-5, "rationale": "...", "turn_citations": []},\n'
            '    "dramatic_arc":          {"score": 1-5, "rationale": "...", "turn_citations": []}\n'
            "  },\n"
            '  "continue_tomorrow": {"decision": <bool>, "reason": "<≤20 words>"},\n'
            '  "closing_paragraph": "<~100 words in your evaluation voice>"\n'
            "}\n```\n"
            "Reasoning before scoring — the rationale comes BEFORE the number "
            "in your thinking.\n\n"
            "## Post-mortem\n\n"
            f"```json\n{json.dumps(post_mortem_json, indent=2, default=str)}\n```"
        )

        cmd = [
            "claude",
            "--resume",
            self.session_id,
            "--print",
            "--output-format",
            "json",
            "--effort",
            self.effort,
            user_message,
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.shell_dir,
                capture_output=True,
                text=True,
                timeout=self.per_turn_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PersonaTimeout(f"final-verdict timeout after {exc.timeout}s") from exc

        if proc.returncode != 0:
            raise PersonaSubprocessError(
                f"final-verdict subprocess returned {proc.returncode}: {proc.stderr[:500]}"
            )

        _, inner = _parse_envelope_and_inner(proc.stdout)

        try:
            return PersonaRunVerdict.model_validate(inner)
        except ValidationError as exc:
            raise PersonaParseError(f"run-verdict output failed validation: {exc}") from exc
