-- Enable RLS on game_sessions and child tables.
--
-- RLS here is the safety net, not the primary authorization gate — FastAPI
-- queries the DB as the service role and filters by user_id in WHERE clauses
-- (see ARCHITECTURE §9, §10). These policies block a malicious client that
-- connects with a user JWT from reading other users' runs.
--
-- No INSERT / UPDATE / DELETE policies: all writes go through the FastAPI
-- service role, never the authenticated Supabase client.

ALTER TABLE public.game_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_read_own_runs" ON public.game_sessions;
CREATE POLICY "users_read_own_runs" ON public.game_sessions
    FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "public_read_shared_runs" ON public.game_sessions;
CREATE POLICY "public_read_shared_runs" ON public.game_sessions
    FOR SELECT USING (share_token IS NOT NULL);

-- agents, turn_logs, consequence_seeds are child tables.  Access is
-- derived from the parent game_sessions.user_id check.

ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_read_agents_for_own_runs" ON public.agents;
CREATE POLICY "users_read_agents_for_own_runs" ON public.agents
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM public.game_sessions gs
        WHERE gs.id = agents.game_id AND (
            gs.user_id = auth.uid() OR gs.share_token IS NOT NULL
        )
    ));

ALTER TABLE public.turn_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_read_turn_logs_for_own_runs" ON public.turn_logs;
CREATE POLICY "users_read_turn_logs_for_own_runs" ON public.turn_logs
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM public.game_sessions gs
        WHERE gs.id = turn_logs.game_id AND (
            gs.user_id = auth.uid() OR gs.share_token IS NOT NULL
        )
    ));

ALTER TABLE public.consequence_seeds ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_read_seeds_for_own_runs" ON public.consequence_seeds;
CREATE POLICY "users_read_seeds_for_own_runs" ON public.consequence_seeds
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM public.game_sessions gs
        WHERE gs.id = consequence_seeds.game_id AND (
            gs.user_id = auth.uid() OR gs.share_token IS NOT NULL
        )
    ));
