-- 017_fees_and_refunds.sql — fees column on payments + dedicated order_refunds table.
-- See plan.md. Idempotent; applied at the top of every merge (db.merge_to_public)
-- so both exist before 005's INSERTs reference them.

-- fees: originalProcessingFee, previously unused in the raw payload.
ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS fees NUMERIC;
ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS fees NUMERIC;

-- order_refunds: dedicated table for payment-level refunds, keyed on Toast's own
-- refund_transaction_guid (a refund is its own event with its own identity, distinct
-- from payment_guid — matches the payment_guid-as-PK convention already used for
-- br_order_payment itself).
CREATE TABLE IF NOT EXISTS staging.order_refunds (
  refund_transaction_guid TEXT,
  payment_guid            TEXT,
  order_guid               TEXT,
  check_guid               TEXT,
  location_code            TEXT,
  refund_date              INT,      -- YYYYMMDD, same encoding as business_date
  refund_amount            NUMERIC,
  tip_refund_amount        NUMERIC
);

CREATE TABLE IF NOT EXISTS public.order_refunds (
  refund_transaction_guid TEXT PRIMARY KEY,
  payment_guid            TEXT NOT NULL,
  order_guid               TEXT NOT NULL,
  check_guid               TEXT NOT NULL,
  location_code            TEXT NOT NULL,
  refund_date              DATE NOT NULL,
  refund_amount            NUMERIC,
  tip_refund_amount        NUMERIC
);
CREATE INDEX IF NOT EXISTS idx_order_refunds_payment ON public.order_refunds (payment_guid);
CREATE INDEX IF NOT EXISTS idx_order_refunds_date    ON public.order_refunds (refund_date);
