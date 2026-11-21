-- Emails from the "Want a free try?" form.
-- Run this in the Supabase SQL editor as the normal role, not "with RLS".

CREATE TABLE IF NOT EXISTS public.emails (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT emails_email_unique UNIQUE (email),
    CONSTRAINT emails_email_format CHECK (
        char_length(email) <= 254
        AND email ~* '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'
    )
);

CREATE OR REPLACE FUNCTION public.normalize_email()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.email := lower(trim(NEW.email));
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS emails_normalize ON public.emails;
CREATE TRIGGER emails_normalize
    BEFORE INSERT OR UPDATE ON public.emails
    FOR EACH ROW
    EXECUTE FUNCTION public.normalize_email();

ALTER TABLE public.emails ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "insert email" ON public.emails;
CREATE POLICY "insert email"
    ON public.emails
    FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);

REVOKE ALL ON FUNCTION public.normalize_email() FROM PUBLIC;
