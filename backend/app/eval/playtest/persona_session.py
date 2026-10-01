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

from app.eval.playtest.prompts import (
    build_count_correction_message as _build_count_correction_message,
)
from app.eval.playtest.prompts import build_system_prompt as _build_system_prompt
from app.eval.playtest.prompts import build_verdict_prompt as _build_verdict_prompt
from app.eval.playtest.schemas import (
    Persona,
    PersonaRunVerdict,
    PersonaTurnOutput,
)
from app.eval.playtest.turn_contract import (
    validate_quotes_against_dashboard as _validate_quotes_against_dashboard,
)
from app.eval.playtest.turn_contract import validate_turn_output as _validate_turn_output
from app.eval.playtest.turn_message import format_turn_user_message as _format_turn_user_message

# Re-exported for tests and callers that predate the prompts/turn_contract split.
__all__ = [
    "PersonaParseError",
    "PersonaSession",
    "PersonaSessionError",
    "PersonaSubprocessError",
    "PersonaTimeout",
    "_build_count_correction_message",
    "_build_system_prompt",
    "_build_verdict_prompt",
    "_format_turn_user_message",
    "_validate_quotes_against_dashboard",
    "_validate_turn_output",
]

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


def _displayed_order_count_mismatch(exc: ValidationError) -> bool:
    """Detect whether a `PersonaTurnOutput` ValidationError fired specifically
    because `displayed_order` had a length other than 3.

    The persona LLM occasionally emits a single-id list (Sonnet 4.6 drift on
    long-context turns); the rest of the schema is fine. Distinguishing this
    failure mode lets us re-prompt with a tightly-targeted nudge — much
    cheaper than a full degrade-to-fallback that costs the persona's pick.
    """
    for err in exc.errors():
        loc = err.get("loc") or ()
        msg = (err.get("msg") or "").lower()
        if loc and loc[0] == "displayed_order" and "exactly 3 ids" in msg:
            return True
    return False


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

    def _run_subprocess(self, cmd: list[str], *, kind: str) -> str:
        """Run one `claude` invocation, returning stdout. Raises typed
        exceptions on timeout / non-zero exit so callers can dispatch."""
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
            raise PersonaTimeout(f"{kind} timeout after {exc.timeout}s") from exc

        if proc.returncode != 0:
            raise PersonaSubprocessError(
                f"{kind} subprocess returned {proc.returncode}: {proc.stderr[:500]}"
            )
        return proc.stdout

    def _resume_cmd(self, user_message: str) -> list[str]:
        """Build the `claude --resume` invocation used by subsequent_turn,
        the count-correction re-prompt, and final_verdict."""
        if self.session_id is None:
            raise RuntimeError("session_id not set — call first_turn() first")
        return [
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

    def _parse_per_turn_with_count_retry(
        self,
        *,
        stdout: str,
        displayed_order: list[str],
        kind: str,
    ) -> PersonaTurnOutput:
        """Parse the per-turn payload from `stdout`. If the only validation
        failure is `displayed_order` count mismatch (sub-bug i), re-prompt
        ONCE with the actual displayed_order echoed verbatim. Any other
        failure (or a second count mismatch) raises `PersonaParseError` so
        the pool's retry/fallback machinery handles it.

        The re-prompt always uses `--resume` because we are guaranteed to
        have a session_id at this point: first_turn captures it from the
        envelope BEFORE attempting validation, and subsequent_turn requires
        one as a precondition.
        """
        _, inner = _parse_envelope_and_inner(stdout)

        try:
            output = PersonaTurnOutput.model_validate(inner)
        except ValidationError as exc:
            if not _displayed_order_count_mismatch(exc):
                raise PersonaParseError(f"per-turn output failed validation: {exc}") from exc

            logger.warning(
                "%s: displayed_order count mismatch on first attempt; "
                "re-prompting with explicit ids %r",
                kind,
                displayed_order,
            )
            correction_msg = _build_count_correction_message(displayed_order=displayed_order)
            retry_stdout = self._run_subprocess(
                self._resume_cmd(correction_msg), kind=f"{kind}-count-retry"
            )
            _, retry_inner = _parse_envelope_and_inner(retry_stdout)
            try:
                output = PersonaTurnOutput.model_validate(retry_inner)
            except ValidationError as retry_exc:
                raise PersonaParseError(
                    f"per-turn output failed validation after count-retry: {retry_exc}"
                ) from retry_exc
        return output

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

        stdout = self._run_subprocess(cmd, kind="first-turn")
        # Capture session_id BEFORE attempting per-turn validation — the
        # count-retry path needs `--resume` and that requires session_id
        # to be set.
        session_id, _ = _parse_envelope_and_inner(stdout)
        self.session_id = session_id

        output = self._parse_per_turn_with_count_retry(
            stdout=stdout, displayed_order=displayed_order, kind="first-turn"
        )
        return _validate_turn_output(output, user_message)

    def subsequent_turn(
        self, *, turn_input: dict[str, Any], displayed_order: list[str]
    ) -> PersonaTurnOutput:
        """Continue an existing session via `claude --resume`."""
        if self.session_id is None:
            raise RuntimeError("no session_id — call first_turn() before subsequent_turn()")

        user_message = _format_turn_user_message(
            turn_input=turn_input, displayed_order=displayed_order
        )
        stdout = self._run_subprocess(self._resume_cmd(user_message), kind="subsequent-turn")
        output = self._parse_per_turn_with_count_retry(
            stdout=stdout, displayed_order=displayed_order, kind="subsequent-turn"
        )
        return _validate_turn_output(output, user_message)

    def final_verdict(self, *, post_mortem_json: dict[str, Any]) -> PersonaRunVerdict:
        """Send the post-mortem JSON to the persona; expect a run-level verdict."""
        if self.session_id is None:
            raise RuntimeError("no session_id — call first_turn() first")

        user_message = _build_verdict_prompt(post_mortem_json)

        stdout = self._run_subprocess(self._resume_cmd(user_message), kind="final-verdict")
        _, inner = _parse_envelope_and_inner(stdout)

        try:
            return PersonaRunVerdict.model_validate(inner)
        except ValidationError as exc:
            raise PersonaParseError(f"run-verdict output failed validation: {exc}") from exc
