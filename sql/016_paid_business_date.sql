-- 016_paid_business_date.sql — paid_business_date on payments (event date vs paid date).
-- See PAYMENT_BASIS_TOGGLE_SPEC.md. Idempotent; applied at the top of every merge
-- (db.merge_to_public) so the column exists before 005's INSERT references it.

ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS paid_business_date INT;
ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS paid_business_date DATE;

CREATE INDEX IF NOT EXISTS idx_bop_paid_bd ON public.br_order_payment (paid_business_date);
