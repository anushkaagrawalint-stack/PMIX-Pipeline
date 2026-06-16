-- 002_staging.sql — parsed, cleaned, validated rows for the current pull.
-- Truncated and rebuilt each run. Grain mirrors public facts.

CREATE TABLE IF NOT EXISTS staging.order_lines (
    selection_guid    TEXT NOT NULL,
    order_guid        TEXT NOT NULL,
    check_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL,
    business_date     INT  NOT NULL,            -- yyyymmdd (local service day)
    item_guid         TEXT,                     -- may be empty: natural key fallback is location_code || raw_name
    item_multi_loc_id TEXT,
    raw_name          TEXT NOT NULL,
    clean_name        TEXT NOT NULL,            -- after name_mappings + normalization
    menu_guid         TEXT,
    menu_name         TEXT,
    menu_group_name   TEXT,
    sales_category    TEXT,
    dining_option     TEXT,                     -- config-API fallback applied
    quantity          NUMERIC NOT NULL,
    line_total        NUMERIC NOT NULL,         -- selection.price: gross, pre-adjustments, pre-tax
    pre_discount      NUMERIC,
    is_voided         BOOLEAN NOT NULL DEFAULT FALSE,
    is_deferred       BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (selection_guid)
);

CREATE TABLE IF NOT EXISTS staging.modifiers (
    modifier_guid     TEXT NOT NULL,
    parent_selection  TEXT NOT NULL,            -- selection_guid of parent (any nesting depth)
    order_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL,
    business_date     INT  NOT NULL,
    raw_name          TEXT NOT NULL,
    clean_name        TEXT NOT NULL,
    depth             INT NOT NULL DEFAULT 1,
    quantity          NUMERIC NOT NULL DEFAULT 1,
    price             NUMERIC NOT NULL DEFAULT 0,
    is_blocklisted    BOOLEAN NOT NULL DEFAULT FALSE,  -- "Please Include Utensils" etc.
    is_voided         BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (modifier_guid, parent_selection)
);

CREATE TABLE IF NOT EXISTS staging.checks (
    check_guid        TEXT PRIMARY KEY,
    order_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL,
    business_date     INT  NOT NULL,
    is_voided         BOOLEAN NOT NULL DEFAULT FALSE,
    tax_amount        NUMERIC,
    total_amount      NUMERIC
);

CREATE TABLE IF NOT EXISTS staging.payments (
    payment_guid      TEXT PRIMARY KEY,
    check_guid        TEXT NOT NULL,
    order_guid        TEXT NOT NULL,
    location_code     TEXT NOT NULL,
    business_date     INT  NOT NULL,
    payment_type      TEXT,                     -- CREDIT | CASH | GIFTCARD | OTHER ...
    alt_payment_name  TEXT,                     -- EzCater, Fooda, ... (drives channel attribution)
    amount            NUMERIC,
    tip_amount        NUMERIC
);

CREATE TABLE IF NOT EXISTS staging.adjustments (
    adjustment_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_guid        TEXT NOT NULL,
    check_guid        TEXT,
    selection_guid    TEXT,
    location_code     TEXT NOT NULL,
    business_date     INT  NOT NULL,
    kind              TEXT NOT NULL,            -- VOID | REFUND | DISCOUNT
    name              TEXT,
    amount            NUMERIC
);
