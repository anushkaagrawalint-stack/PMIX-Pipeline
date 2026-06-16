-- 004_public_facts.sql — production facts.
-- Grain of fact_order_lines = 1 row per selection per check (Toast line item).
-- Voids are PRESERVED with is_voided for net-vs-gross analysis; every revenue
-- view must carry WHERE NOT is_voided.

CREATE TABLE IF NOT EXISTS public.fact_order_lines (
    selection_guid    TEXT PRIMARY KEY,
    order_guid        TEXT NOT NULL,
    check_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL REFERENCES public.dim_location(location_code),
    business_date     DATE NOT NULL,
    item_key          BIGINT REFERENCES public.dim_item(item_key),
    canonical_name    TEXT NOT NULL,
    menu_name         TEXT,
    menu_group        TEXT,
    sales_category    TEXT,
    dining_option     TEXT,
    channel_code      TEXT NOT NULL REFERENCES public.dim_channel(channel_code),
    quantity          NUMERIC NOT NULL,
    line_total        NUMERIC NOT NULL,          -- gross, pre-adjustments, pre-tax, excludes service charges
    pre_discount      NUMERIC,
    is_voided         BOOLEAN NOT NULL,
    is_deferred       BOOLEAN NOT NULL,
    pull_run_id       BIGINT
);
CREATE INDEX IF NOT EXISTS idx_fol_date ON public.fact_order_lines (business_date, location_code);
CREATE INDEX IF NOT EXISTS idx_fol_item ON public.fact_order_lines (canonical_name, business_date);
CREATE INDEX IF NOT EXISTS idx_fol_channel ON public.fact_order_lines (channel_code, business_date);

CREATE TABLE IF NOT EXISTS public.fact_modifiers (
    modifier_guid     TEXT NOT NULL,
    parent_selection  TEXT NOT NULL,
    order_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL,
    business_date     DATE NOT NULL,
    modifier_key      BIGINT REFERENCES public.dim_modifier(modifier_key),
    canonical_name    TEXT NOT NULL,
    mod_type          TEXT,
    depth             INT NOT NULL,
    quantity          NUMERIC NOT NULL,
    price             NUMERIC NOT NULL,
    is_voided         BOOLEAN NOT NULL,
    PRIMARY KEY (modifier_guid, parent_selection)
);
CREATE INDEX IF NOT EXISTS idx_fm_parent ON public.fact_modifiers (parent_selection);
CREATE INDEX IF NOT EXISTS idx_fm_name ON public.fact_modifiers (canonical_name, business_date);

CREATE TABLE IF NOT EXISTS public.fact_checks (
    check_guid        TEXT PRIMARY KEY,
    order_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL,
    business_date     DATE NOT NULL,
    is_voided         BOOLEAN NOT NULL,
    tax_amount        NUMERIC,
    total_amount      NUMERIC
);

CREATE TABLE IF NOT EXISTS public.br_order_payment (
    payment_guid      TEXT PRIMARY KEY,
    check_guid        TEXT NOT NULL,
    order_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL,
    business_date     DATE NOT NULL,
    payment_type      TEXT,
    alt_payment_name  TEXT,
    amount            NUMERIC,
    tip_amount        NUMERIC
);
CREATE INDEX IF NOT EXISTS idx_bop_alt ON public.br_order_payment (alt_payment_name);

CREATE TABLE IF NOT EXISTS public.fact_adjustments (
    adjustment_key    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_guid        TEXT NOT NULL,
    check_guid        TEXT,
    selection_guid    TEXT,
    location_code     TEXT NOT NULL,
    business_date     DATE NOT NULL,
    kind              TEXT NOT NULL,
    name              TEXT,
    amount            NUMERIC
);

-- ---------------------------------------------------------------------------
-- Kutlerri analytics layer lands in a dedicated schema later (phase 4+):
--   analytics.dim_recipe_cost      (R365, period grain)
--   analytics.fact_retention       (Bikky, period grain)
--   analytics.mv_menu_engineering  (thresholds + quadrants)
-- Kept separate so the ingestion layer and analytics layer evolve independently.
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS analytics;
