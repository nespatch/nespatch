-- PDF translation credits.
-- Separate from the slippers wallet in supabase_wallet.sql.
-- Run this in the Supabase SQL editor.
--
-- A signed-in user can read their own balance. Adding or spending
-- credits is not granted to the browser; that stays for a later
-- server-side function.

CREATE TABLE IF NOT EXISTS public.credits (
    user_id UUID PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.credits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "read own credits" ON public.credits;
CREATE POLICY "read own credits"
    ON public.credits
    FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

-- One Paddle transaction adds credits once.
CREATE TABLE IF NOT EXISTS public.credit_payments (
    transaction_id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    files INTEGER NOT NULL CHECK (files > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.grant_credits(
    p_user UUID,
    p_files INT,
    p_transaction TEXT
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    bal INT;
BEGIN
    IF p_files IS NULL OR p_files < 1 THEN
        RAISE EXCEPTION 'files must be positive';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.credit_payments WHERE transaction_id = p_transaction
    ) THEN
        SELECT balance INTO bal FROM public.credits WHERE user_id = p_user;
        RETURN COALESCE(bal, 0);
    END IF;

    INSERT INTO public.credit_payments (transaction_id, user_id, files)
    VALUES (p_transaction, p_user, p_files);

    INSERT INTO public.credits (user_id, balance, updated_at)
    VALUES (p_user, p_files, now())
    ON CONFLICT (user_id) DO UPDATE
        SET balance = public.credits.balance + EXCLUDED.balance,
            updated_at = now();

    SELECT balance INTO bal FROM public.credits WHERE user_id = p_user;
    RETURN bal;
END;
$$;

REVOKE ALL ON FUNCTION public.grant_credits(UUID, INT, TEXT) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.grant_credits(UUID, INT, TEXT) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.grant_credits(UUID, INT, TEXT) TO service_role;
