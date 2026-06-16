-- 001_init.sql — schemas + raw landing + run tracking
-- Three-schema trust boundary: raw (immutable) -> staging (rebuilt per run) -> public (star schema)

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
-- public exists by default

-- ---------------------------------------------------------------------------
-- Run tracking: every pipeline invocation opens a pull_run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.pull_runs (
    pull_run_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | success | failed
    window_start    DATE NOT NULL,                    -- business-date window requested
    window_end      DATE NOT NULL,
    locations       TEXT[] NOT NULL,
    orders_fetched  INT NOT NULL DEFAULT 0,
    orders_upserted INT NOT NULL DEFAULT 0,
    error           TEXT
);

-- ---------------------------------------------------------------------------
-- Raw Toast payloads. Untouched JSON; the audit trail and re-parse source.
-- One row per order_guid; payload replaced when Toast modifiedDate advances.
-- NOTE: Toast's startDate/endDate filter on ordersBulk uses the *modified*
-- timestamp, not business date — we over-fetch and attribute by businessDate.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.toast_orders (
    order_guid      TEXT PRIMARY KEY,
    location_code   TEXT NOT NULL,
    business_date   INT,                              -- yyyymmdd from payload.businessDate
    modified_date   TIMESTAMPTZ,
    payload         JSONB NOT NULL,
    pull_run_id     BIGINT NOT NULL REFERENCES raw.pull_runs(pull_run_id),
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_raw_orders_bizdate ON raw.toast_orders (location_code, business_date);
CREATE INDEX IF NOT EXISTS idx_raw_orders_modified ON raw.toast_orders (modified_date);

-- ---------------------------------------------------------------------------
-- Raw config-API snapshots (dining options, menus, sales categories,
-- alternate payment types). Re-fetched per run; history kept per pull_run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.toast_config (
    config_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    location_code   TEXT NOT NULL,
    config_type     TEXT NOT NULL,  -- dining_options | menus | sales_categories | alt_payment_types
    payload         JSONB NOT NULL,
    pull_run_id     BIGINT NOT NULL REFERENCES raw.pull_runs(pull_run_id),
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_raw_config_lookup ON raw.toast_config (location_code, config_type, pull_run_id);
