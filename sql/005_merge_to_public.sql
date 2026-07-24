-- 005_merge_to_public.sql — upsert staging into public.
-- ON CONFLICT semantics: selections are immutable once written for a guid,
-- but Toast orders get modified (voids, refunds) — so we DO UPDATE on the
-- mutable flags/amounts. Overlapping fetch windows dedupe safely on PK.

-- ---------------------------------------------------------------------------
-- 1. Upsert dim_item from staging
-- ---------------------------------------------------------------------------
INSERT INTO public.dim_item (natural_key, item_guid, multi_loc_id, canonical_name,
                             menu_name, menu_group, sales_category, first_seen, last_seen)
SELECT DISTINCT ON (COALESCE(NULLIF(s.item_guid,''), s.location_code||'::'||s.raw_name))
       COALESCE(NULLIF(s.item_guid,''), s.location_code||'::'||s.raw_name),
       NULLIF(s.item_guid,''),
       s.item_multi_loc_id,
       s.clean_name,
       s.menu_name, s.menu_group_name, s.sales_category,
       to_date(s.business_date::text,'YYYYMMDD'),
       to_date(s.business_date::text,'YYYYMMDD')
FROM staging.order_lines s
ORDER BY COALESCE(NULLIF(s.item_guid,''), s.location_code||'::'||s.raw_name), s.business_date DESC
ON CONFLICT (natural_key) DO UPDATE SET
    canonical_name = EXCLUDED.canonical_name,
    menu_group     = COALESCE(EXCLUDED.menu_group, public.dim_item.menu_group),
    last_seen      = GREATEST(public.dim_item.last_seen, EXCLUDED.last_seen);

-- ---------------------------------------------------------------------------
-- 2. Upsert fact_order_lines with channel attribution.
--    Default CASE logic, COALESCE'd under manual channel_override.
--    TODO(phase 3): refine cases against the reconciliation spike findings.
-- ---------------------------------------------------------------------------
INSERT INTO public.fact_order_lines (
    selection_guid, order_guid, check_guid, location_code, business_date,
    item_key, canonical_name, menu_name, menu_group, sales_category,
    dining_option, channel_code, quantity, line_total, pre_discount,
    is_voided, is_deferred, pull_run_id)
SELECT
    s.selection_guid, s.order_guid, s.check_guid, s.location_code,
    to_date(s.business_date::text,'YYYYMMDD'),
    di.item_key, s.clean_name, s.menu_name, s.menu_group_name, s.sales_category,
    s.dining_option,
    COALESCE(
        co.channel_code,
        CASE
            WHEN p.alt_payment_name ILIKE ANY (ARRAY['%ezcater%','%ez cater%','%hungry%','%sharebite%','%territory%','%cater 2 me%','%zerocater%','%cater cow%','%wck%','%food fleet%'])
                THEN 'CATERING'
            WHEN p.alt_payment_name ILIKE ANY (ARRAY['%fooda%','%aramark%','%eurest%','%metz%','%taher%','%foodworks%','%cureate%','%guest services%'])
                THEN 'OFFSITE'
            WHEN p.alt_payment_name ILIKE ANY (ARRAY['%doordash%','%uber%','%grubhub%','%postmates%'])
                THEN 'TPD'
            WHEN s.dining_option ILIKE '%catering%' OR s.sales_category ILIKE '%catering%'
                THEN 'CATERING'
            WHEN s.dining_option ILIKE '%open app%' OR s.dining_option ILIKE '%online ordering%'
                THEN 'APP'
            ELSE 'IN_HOUSE'
        END),
    s.quantity, s.line_total, s.pre_discount, s.is_voided, s.is_deferred,
    (SELECT max(pull_run_id) FROM raw.pull_runs)
FROM staging.order_lines s
LEFT JOIN public.dim_item di
       ON di.natural_key = COALESCE(NULLIF(s.item_guid,''), s.location_code||'::'||s.raw_name)
LEFT JOIN public.channel_override co ON co.order_guid = s.order_guid
LEFT JOIN LATERAL (
    SELECT sp.alt_payment_name
    FROM staging.payments sp
    WHERE sp.order_guid = s.order_guid AND sp.alt_payment_name IS NOT NULL
    ORDER BY sp.amount DESC NULLS LAST LIMIT 1
) p ON TRUE
ON CONFLICT (selection_guid) DO UPDATE SET
    is_voided      = EXCLUDED.is_voided,
    is_deferred    = EXCLUDED.is_deferred,
    line_total     = EXCLUDED.line_total,
    channel_code   = EXCLUDED.channel_code,
    canonical_name = EXCLUDED.canonical_name,
    menu_name      = COALESCE(EXCLUDED.menu_name, public.fact_order_lines.menu_name),
    menu_group     = COALESCE(EXCLUDED.menu_group, public.fact_order_lines.menu_group),
    sales_category = COALESCE(EXCLUDED.sales_category, public.fact_order_lines.sales_category),
    dining_option  = COALESCE(EXCLUDED.dining_option, public.fact_order_lines.dining_option);

-- ---------------------------------------------------------------------------
-- 3. Modifiers, checks, payments, adjustments
-- ---------------------------------------------------------------------------
INSERT INTO public.fact_modifiers (modifier_guid, parent_selection, order_guid,
    location_code, business_date, canonical_name, mod_type, depth, quantity, price, is_voided,
    option_group_guid, option_group_name)
SELECT m.modifier_guid, m.parent_selection, m.order_guid, m.location_code,
       to_date(m.business_date::text,'YYYYMMDD'),
       m.clean_name, dm.mod_type, m.depth, m.quantity, m.price, m.is_voided,
       m.option_group_guid, m.option_group_name
FROM staging.modifiers m
LEFT JOIN public.dim_modifier dm ON dm.canonical_name = m.clean_name
WHERE NOT m.is_blocklisted
ON CONFLICT (modifier_guid, parent_selection) DO UPDATE SET
    is_voided          = EXCLUDED.is_voided,
    canonical_name     = EXCLUDED.canonical_name,
    option_group_guid  = EXCLUDED.option_group_guid,
    option_group_name  = EXCLUDED.option_group_name;

INSERT INTO public.fact_checks
SELECT c.check_guid, c.order_guid, c.location_code,
       to_date(c.business_date::text,'YYYYMMDD'), c.is_voided, c.tax_amount, c.total_amount
FROM staging.checks c
ON CONFLICT (check_guid) DO UPDATE SET
    is_voided = EXCLUDED.is_voided, total_amount = EXCLUDED.total_amount;

INSERT INTO public.br_order_payment
    (payment_guid, check_guid, order_guid, location_code, business_date,
     payment_type, alt_payment_name, amount, tip_amount, paid_status,
     paid_business_date, fees, withholdings)
SELECT p.payment_guid, p.check_guid, p.order_guid, p.location_code,
       to_date(p.business_date::text,'YYYYMMDD'),
       p.payment_type, p.alt_payment_name, p.amount, p.tip_amount,
       p.paid_status,
       CASE WHEN p.paid_business_date IS NULL THEN NULL
            ELSE to_date(p.paid_business_date::text,'YYYYMMDD') END,
       p.fees, p.withholdings
FROM staging.payments p
ON CONFLICT (payment_guid) DO UPDATE SET
    payment_type       = EXCLUDED.payment_type,
    alt_payment_name   = EXCLUDED.alt_payment_name,
    amount             = EXCLUDED.amount,
    tip_amount         = EXCLUDED.tip_amount,
    paid_status        = EXCLUDED.paid_status,
    paid_business_date = EXCLUDED.paid_business_date,
    fees               = EXCLUDED.fees,
    withholdings       = EXCLUDED.withholdings;

INSERT INTO public.fact_adjustments (order_guid, check_guid, selection_guid,
    location_code, business_date, kind, name, amount)
SELECT a.order_guid, a.check_guid, a.selection_guid, a.location_code,
       to_date(a.business_date::text,'YYYYMMDD'), a.kind, a.name, a.amount
FROM staging.adjustments a;

-- order_refunds: payment-level refunds, dated by when the refund itself happened
-- (refund_date), not the original payment's business_date. See plan.md.
INSERT INTO public.order_refunds
    (refund_transaction_guid, payment_guid, order_guid, check_guid, location_code,
     refund_date, refund_amount, tip_refund_amount)
SELECT r.refund_transaction_guid, r.payment_guid, r.order_guid, r.check_guid,
       r.location_code, to_date(r.refund_date::text,'YYYYMMDD'),
       r.refund_amount, r.tip_refund_amount
FROM staging.order_refunds r
WHERE r.refund_transaction_guid IS NOT NULL AND r.refund_transaction_guid <> ''
ON CONFLICT (refund_transaction_guid, payment_guid) DO UPDATE SET
    refund_date       = EXCLUDED.refund_date,
    refund_amount      = EXCLUDED.refund_amount,
    tip_refund_amount  = EXCLUDED.tip_refund_amount;
