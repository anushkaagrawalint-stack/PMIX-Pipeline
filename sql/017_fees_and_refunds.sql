-- 017_fees_and_refunds.sql — fees column on payments + dedicated order_refunds table.
-- See plan.md. Idempotent; applied at the top of every merge (db.merge_to_public)
-- so both exist before 005's INSERTs reference them.

-- fees: originalProcessingFee, previously unused in the raw payload.
ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS fees NUMERIC;
ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS fees NUMERIC;

-- withholdings: mcaRepaymentAmount (merchant cash advance repayment, withheld
-- from payout automatically) — confirmed genuinely nonzero in the raw data
-- (30 payments), unlike chargebacks, which have no backing field anywhere in
-- the Orders API payload (that data lives in Toast's separate Cash Management
-- API, which this pipeline doesn't call).
ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS withholdings NUMERIC;
ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS withholdings NUMERIC;

-- order_refunds: dedicated table for payment-level refunds. Keyed on
-- (refund_transaction_guid, payment_guid) — NOT refund_transaction_guid alone,
-- since a single refund transaction can span multiple payments within the
-- same check (a split-tender check refunded in one transaction shares one
-- refundTransaction.guid across each payment's own refund object).
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
  refund_transaction_guid TEXT NOT NULL,
  payment_guid            TEXT NOT NULL,
  order_guid               TEXT NOT NULL,
  check_guid               TEXT NOT NULL,
  location_code            TEXT NOT NULL,
  refund_date              DATE NOT NULL,
  refund_amount            NUMERIC,
  tip_refund_amount        NUMERIC,
  PRIMARY KEY (refund_transaction_guid, payment_guid)
);
CREATE INDEX IF NOT EXISTS idx_order_refunds_payment ON public.order_refunds (payment_guid);
CREATE INDEX IF NOT EXISTS idx_order_refunds_date    ON public.order_refunds (refund_date);
