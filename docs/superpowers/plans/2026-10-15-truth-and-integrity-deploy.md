# Deploy checklist: truth & integrity

Order is mandatory (spec §9):
1. Run `uv run alembic upgrade head` against prod (adds nullable `turn_logs.tool_calls` and `turn_logs.turn_meta`; safe for the old backend).
   If prod's direct connect (5432) is unreachable over IPv6, use the fallback in `backend/CLAUDE.md` (Migrations): apply via the Supabase MCP `apply_migration` and seed `public.alembic_version`.
2. Confirm `alembic current` on prod shows `a7c3e9d41f20` (on the fallback path, check the `public.alembic_version` table instead).
3. Deploy the backend. Without step 1, every `select(TurnLog)` fails: post-mortems, share links and turn persistence.
4. Deploy the frontend.

Known separate blocker: Railway lacks the `LLM_*` variables.
In-flight games: one turn may show the old LLM previews after the deploy; accepted.
