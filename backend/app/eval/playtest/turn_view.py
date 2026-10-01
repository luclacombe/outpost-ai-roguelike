"""Per-turn view handed to a persona: parity with the live dashboard.

Every field here maps to something `frontend/components/dashboard/*` renders
during continuous play. Nothing hidden by the UI is included.

| View field            | UI source                                                 |
|-----------------------|-----------------------------------------------------------|
| day / max_turns       | DashboardHeader TurnBadge ("DAY 00 / 9", 0-based day)     |
| setting_name          | DashboardHeader title                                     |
| legitimacy (+ tone)   | LegitimacyMeter (ok >=60, warning >=30, danger <30)       |
| resources value/max   | ResourceBar (tone: low <= critical, critical <= crit/2)   |
| advisors status_label | AgentRoster StatusPill (KIA/WOUNDED/STEWING/TIRED/STEADY) |
| event (+ meta)        | EventDebatePanel header: category, severity, CONSEQUENCE  |
| event_log             | EventLog: event titles lowercased + "<name> died."        |
| advisor_reactions     | EventDebatePanel DEBATE lines                             |
| last_turn.narration   | EventDebatePanel AFTER block (held by the Continue gate)  |
| proposals             | ChoosePanel: name, flavor, proposer, description,         |
|                       | effects_preview                                           |

`last_turn` deltas are diffs of the visible numbers above between two
consecutive views, so the persona learns nothing a player watching the
bars could not. Deliberately omitted because the UI never renders them:
player_health, raw grievance/morale/health numbers, autonomous actions
(`autonomous_action` frames are dropped by useTurnStream), rejected or
clamped tool calls, critical-threshold numbers (shown only as bar colour).

Values come from `game_sessions.state` (written at `advance_turn`), which is
what a fresh `/game/{id}` load shows. The live dashboard does not refresh
resources/advisors from SSE mid-run (see DashboardShell), so a player who
never reloads sees stale bars; the persona sees the fresh-load values.
"""

from __future__ import annotations

from typing import Any

from app.game.schemas import Agent, ColonyState
from app.game.setting_loader import load_setting


def status_pill(agent: Agent) -> str:
    """Same precedence as AgentRoster.derive()."""
    if not agent.is_alive:
        return "KIA"
    if agent.health < 30:
        return "WOUNDED"
    if agent.grievance >= 7:
        return "STEWING"
    if agent.morale < 30:
        return "TIRED"
    return "STEADY"


def legitimacy_tone(value: int) -> str:
    """LegitimacyMeter colour bands."""
    if value >= 60:
        return "ok"
    if value >= 30:
        return "warning"
    return "danger"


def resource_tone(*, value: int, critical_threshold: int) -> str:
    """ResourceBar colour: warning at/below critical, pulsing at/below half."""
    if value <= max(1, critical_threshold // 2):
        return "critical"
    if value <= critical_threshold:
        return "low"
    return "ok"


def _setting_text(setting_id: str) -> tuple[str, str]:
    try:
        setting = load_setting(setting_id)
    except FileNotFoundError:
        return setting_id, ""
    return setting.name, setting.lore


class TurnViewTracker:
    """Folds SSE frames into the persona's per-turn view, one game at a time.

    Call order per turn: `absorb(turn_frames)` → `build(...)` →
    `record_choice(proposal)` → `absorb(decide_frames)`. `/decide` carries
    the narration and deaths for the turn just played, plus the next turn's
    `proposals_ready`; the next `/turn` short-circuits and re-emits that
    event, so log entries are de-duplicated by event id like useTurnStream.
    """

    def __init__(self) -> None:
        self.event_log: list[dict[str, Any]] = []
        self._seen_event_ids: set[str] = set()
        self._prev_dashboard: dict[str, Any] | None = None
        self._last_choice: dict[str, Any] | None = None
        self._narration: str | None = None
        self._deaths: list[str] = []

    def absorb(self, frames: list[dict[str, Any]]) -> None:
        for f in frames:
            data = f.get("data")
            if not isinstance(data, dict):
                continue
            event = f.get("event")
            if event == "proposals_ready":
                ev = data.get("current_event") or {}
                eid = ev.get("id")
                if eid and eid not in self._seen_event_ids:
                    self._seen_event_ids.add(eid)
                    self.event_log.append(
                        {"turn": data.get("turn_number"), "text": str(ev.get("title", "")).lower()}
                    )
            elif event == "death":
                name = data.get("agent_name", "?")
                self._deaths.append(name)
                self.event_log.append({"turn": data.get("turn"), "text": f"{name} died."})
            elif event == "narration" and data.get("text"):
                self._narration = data["text"]

    def record_choice(self, proposal: dict[str, Any]) -> None:
        self._last_choice = {
            "name": proposal.get("name"),
            "flavor": proposal.get("flavor"),
            "effects_preview": list(proposal.get("effects_preview") or []),
        }

    def _last_turn(self, dashboard: dict[str, Any]) -> dict[str, Any] | None:
        prev = self._prev_dashboard
        if prev is None or self._last_choice is None:
            return None
        prev_res = {r["id"]: r for r in prev["resources"]}
        resource_deltas = {
            r["name"]: r["value"] - prev_res[r["id"]]["value"]
            for r in dashboard["resources"]
            if r["id"] in prev_res and r["value"] != prev_res[r["id"]]["value"]
        }
        prev_status = {a["id"]: a["status_label"] for a in prev["advisors"]}
        status_changes = [
            {"name": a["name"], "from": prev_status[a["id"]], "to": a["status_label"]}
            for a in dashboard["advisors"]
            if a["id"] in prev_status and prev_status[a["id"]] != a["status_label"]
        ]
        return {
            "chosen": self._last_choice,
            "narration": self._narration,
            "deaths": list(self._deaths),
            "legitimacy_delta": dashboard["legitimacy"] - prev["legitimacy"],
            "resource_deltas": resource_deltas,
            "status_changes": status_changes,
        }

    def build(
        self,
        *,
        turn: int,
        colony: ColonyState,
        turn_frames: list[dict[str, Any]],
        proposals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Assemble the view for the pause the harness is sitting at."""
        pr = next((f for f in turn_frames if f["event"] == "proposals_ready"), None)
        pr_data = pr["data"] if pr else {}
        setting_name, setting_lore = _setting_text(colony.setting_id)

        dashboard = {
            "legitimacy": colony.legitimacy,
            "resources": [
                {
                    "id": r.id,
                    "name": r.name,
                    "value": r.value,
                    "max": r.max,
                    "critical_threshold": r.critical_threshold,
                }
                for r in colony.resources.values()
            ],
            "advisors": [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "role": a.role,
                    "is_alive": a.is_alive,
                    "status_label": status_pill(a),
                }
                for a in colony.agents
            ],
        }
        view = {
            "turn": turn,
            "day": pr_data.get("turn_number"),
            "max_turns": colony.max_turns,
            "setting_name": setting_name,
            "setting_lore": setting_lore,
            "dashboard": dashboard,
            "event": pr_data.get("current_event") or {},
            "event_log": list(self.event_log),
            "last_turn": self._last_turn(dashboard),
            "proposals": proposals,
            "advisor_reactions": [
                {"agent_id": f["data"]["agent_id"], "text": f["data"]["text"]}
                for f in turn_frames
                if f["event"] == "agent_spoke" and isinstance(f.get("data"), dict)
            ],
        }
        self._prev_dashboard = dashboard
        self._narration = None
        self._deaths = []
        return view
