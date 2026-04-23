-- Enable RLS on profiles and wire the "create profile on signup" trigger.
--
-- RLS here is the safety net, not the primary authorization gate — FastAPI
-- queries the DB as the service role and filters by user_id in WHERE
-- clauses (see app/dependencies.py::get_db and ARCHITECTURE §9, §10). The
-- policies below stop a malicious client that somehow connects with a user
-- JWT from reading or updating other users' rows.

-- Profiles row-level security
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users_read_own_profile" ON public.profiles;
CREATE POLICY "users_read_own_profile" ON public.profiles
    FOR SELECT USING (auth.uid() = id);

DROP POLICY IF EXISTS "users_update_own_profile" ON public.profiles;
CREATE POLICY "users_update_own_profile" ON public.profiles
    FOR UPDATE USING (auth.uid() = id);

-- Bootstrap: every new auth.users row gets a matching profiles row so the
-- app can depend on the invariant "authenticated user => profile exists".
-- SECURITY DEFINER is required because the trigger runs as the postgres
-- role; we lock search_path to avoid function-hijacking via schema search.
CREATE OR REPLACE FUNCTION public.create_profile_for_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, display_name)
    VALUES (NEW.id, NEW.raw_user_meta_data->>'name')
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.create_profile_for_new_user();
