-- Create profiles table.
--
-- This migration mirrors the Alembic revision c569693a0392.
-- It must run before any RLS or trigger migrations that reference profiles.
-- Alembic tracks this table in alembic_version and will detect it as
-- already-existing (CREATE TABLE IF NOT EXISTS) on first run.
--
-- The FK to auth.users ensures a profile can only exist for a real user.
-- Rows are seeded by the on_auth_user_created trigger (next migration).

CREATE TABLE IF NOT EXISTS public.profiles (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name TEXT,
    last_run_started_at TIMESTAMPTZ,
    total_runs  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
