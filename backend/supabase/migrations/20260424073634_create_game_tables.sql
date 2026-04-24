-- Create game tables.
--
-- Mirrors the Alembic revision 3ba6ba8568f0.
-- Must run before the RLS migration that enables row-level security on these
-- tables.  Alembic's alembic_version is stamped separately after first
-- deploy; Alembic's --autogenerate will see these tables as already-existing
-- and produce no new ops.

CREATE TABLE IF NOT EXISTS public.game_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    setting_id      TEXT NOT NULL,
    seed            BIGINT NOT NULL,
    max_turns       INTEGER NOT NULL,
    current_turn    INTEGER NOT NULL DEFAULT 0,
    state           JSONB NOT NULL,
    legitimacy      INTEGER NOT NULL DEFAULT 100,
    player_health   INTEGER NOT NULL DEFAULT 100,
    end_state       TEXT,
    cost_cents      INTEGER NOT NULL DEFAULT 0,
    share_token     TEXT UNIQUE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_game_sessions_user ON public.game_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_end_state
    ON public.game_sessions (end_state)
    WHERE end_state IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.agents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id             UUID NOT NULL REFERENCES public.game_sessions(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    role                TEXT NOT NULL,
    archetype_quadrant  TEXT NOT NULL,
    ethics              REAL NOT NULL,
    temperament         REAL NOT NULL,
    loyalty             REAL NOT NULL,
    portrait_seed       TEXT NOT NULL,
    state               JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agents_game ON public.agents (game_id);

CREATE TABLE IF NOT EXISTS public.turn_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id         UUID NOT NULL REFERENCES public.game_sessions(id) ON DELETE CASCADE,
    turn_number     INTEGER NOT NULL,
    event           JSONB NOT NULL,
    proposals       JSONB NOT NULL,
    chosen          JSONB NOT NULL,
    agent_responses JSONB NOT NULL,
    autonomous_actions JSONB,
    narration       TEXT,
    state_snapshot  JSONB NOT NULL,
    turn_cost_cents INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (game_id, turn_number)
);

CREATE TABLE IF NOT EXISTS public.consequence_seeds (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    game_id           UUID NOT NULL REFERENCES public.game_sessions(id) ON DELETE CASCADE,
    written_at_turn   INTEGER NOT NULL,
    fires_at_turn     INTEGER NOT NULL,
    foreshadowed      BOOLEAN NOT NULL,
    foreshadow_turn   INTEGER,
    payload           JSONB NOT NULL,
    origin_decision   JSONB NOT NULL,
    fired             BOOLEAN NOT NULL DEFAULT FALSE,
    dormant           BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_seeds_game_fires
    ON public.consequence_seeds (game_id, fires_at_turn);
