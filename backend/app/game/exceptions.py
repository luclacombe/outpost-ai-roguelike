"""Domain errors raised by the game service layer.

Routers map these to HTTP responses; the service raises plain Python
exceptions so business logic stays framework-agnostic. The error envelope
matches the convention in root CLAUDE.md:
  {"error": {"code": "...", "message": "...", "detail": {...}}}
"""

from __future__ import annotations


class GameError(Exception):
    """Base class for domain errors surfaced through the game API."""

    code: str = "game_error"
    message: str = "Game error."

    def __init__(self, message: str | None = None, detail: dict | None = None) -> None:
        self.message = message or self.__class__.message
        self.detail = detail
        super().__init__(self.message)

    def to_dict(self) -> dict:
        out: dict = {"code": self.code, "message": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


class DailyGateExceeded(GameError):  # noqa: N818 — named for the 429 code, not as a generic "Error"
    """Raised when the one-run-per-day rule blocks session creation.

    Constructor takes the `next_run_at` ISO-8601 string so the router can
    surface it verbatim in the 429 response.
    """

    code = "daily_gate_exceeded"

    def __init__(self, next_run_at_iso: str) -> None:
        super().__init__(
            message=f"Your next run begins at {next_run_at_iso}",
            detail={"next_run_at": next_run_at_iso},
        )


class GameNotFound(GameError):  # noqa: N818 — names the 404 condition, not an "Error" class
    """Raised for GET /game/{id} when the run does not exist OR belongs to
    a different user.

    Same error either way to avoid leaking existence of other users' runs.
    """

    code = "game_not_found"
    message = "game_not_found"


class GameNotEnded(GameError):  # noqa: N818 — names the 409 condition
    """Raised when a post-mortem is requested for a still-running game.

    The post-mortem aggregates terminal state (end_state, final scores,
    counterfactuals) — surfacing it mid-run would either confuse the player
    or leak future events. Surfaces as 409 Conflict.
    """

    code = "game_not_ended"
    message = "Run is still in progress."


class NoSettingsAvailable(GameError):  # noqa: N818 — names the 503 condition
    """Raised when the setting pool is empty or all fail to load/validate.

    Surfaces as 503 — the launch settings are part of the build so this
    indicates a deployment defect, not a user error.
    """

    code = "no_settings_available"
    message = "No playable settings are available."
