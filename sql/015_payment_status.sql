-- 015_payment_status.sql — paid_status + refund_amount on payments.
-- See PAYMENT_STATUS_SPEC.md. Idempotent; applied at the top of every merge
-- (db.merge_to_public) so the columns exist before 005's INSERT references them.

ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS paid_status TEXT,
                                    ADD COLUMN IF NOT EXISTS refund_amount NUMERIC;
ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS paid_status TEXT,
                                    ADD COLUMN IF NOT EXISTS refund_amount NUMERIC;
