-- Read-only, LLM-friendly views over payment data for the dashboard's
-- natural-language "Ask" feature (Payments tab, admin/tester only).
--
-- Kept as two separate views rather than one joined view because
-- br_order_payment/order_refunds mix two independent date dimensions -- a
-- payment's own business_date vs a refund's separate refund_date -- and
-- conflating them was the root cause of a real reporting bug (dashboard
-- commit 858b3c1: a refund was silently dropped because it was bucketed by
-- the original payment's date instead of its own refund_date). Keeping that
-- distinction explicit in the schema a model sees prevents it from making
-- the same mistake.

DROP VIEW IF EXISTS analytics.v_payments_llm;
CREATE VIEW analytics.v_payments_llm AS
SELECT
  p.payment_guid,
  p.order_guid,
  p.location_code,
  COALESCE(dl.display_name, p.location_code)                              AS location_name,
  p.business_date,
  p.paid_business_date,
  p.payment_type,
  CASE WHEN p.payment_type = 'CREDIT' THEN 'Card' ELSE 'Alt Payment' END   AS category,
  COALESCE(NULLIF(TRIM(p.alt_payment_name), ''), p.payment_type, 'Unknown') AS payment_source,
  p.amount,
  p.tip_amount,
  p.fees,
  p.withholdings,
  COALESCE(p.paid_status, 'CAPTURED')                                     AS paid_status
FROM public.br_order_payment p
LEFT JOIN public.dim_location dl ON dl.location_code = p.location_code;

DROP VIEW IF EXISTS analytics.v_refunds_llm;
CREATE VIEW analytics.v_refunds_llm AS
SELECT
  r.refund_transaction_guid,
  r.payment_guid,
  r.order_guid,
  r.location_code,
  COALESCE(dl.display_name, r.location_code) AS location_name,
  r.refund_date,
  r.refund_amount,
  r.tip_refund_amount,
  bop.payment_type,
  COALESCE(NULLIF(TRIM(bop.alt_payment_name), ''), bop.payment_type, 'Unknown') AS payment_source
FROM public.order_refunds r
LEFT JOIN public.br_order_payment bop ON bop.payment_guid = r.payment_guid
LEFT JOIN public.dim_location dl ON dl.location_code = r.location_code;
