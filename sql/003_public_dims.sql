-- 003_public_dims.sql — production dimensions.
-- v0.1 keeps dims simple (current-state). TODO: promote dim_item/dim_modifier
-- to SCD-2 (valid_from/valid_to) once the merge loop is stable.

CREATE TABLE IF NOT EXISTS public.dim_location (
    location_code   TEXT PRIMARY KEY,            -- BALLPARK | MVT | NL | MOSAIC | ROCKVILLE
    toast_guid      TEXT UNIQUE,
    display_name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS public.dim_item (
    item_key        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    natural_key     TEXT NOT NULL UNIQUE,        -- item_guid, else location_code||'::'||raw_name
    item_guid       TEXT,
    multi_loc_id    TEXT,
    canonical_name  TEXT NOT NULL,               -- post name_mappings; renames consolidate here
    menu_name       TEXT,
    menu_group      TEXT,                        -- refreshed each run from Menus API
    sales_category  TEXT,
    first_seen      DATE,
    last_seen       DATE
);
CREATE INDEX IF NOT EXISTS idx_dim_item_canonical ON public.dim_item (canonical_name);

CREATE TABLE IF NOT EXISTS public.dim_modifier (
    modifier_key    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    natural_key     TEXT NOT NULL UNIQUE,
    canonical_name  TEXT NOT NULL,
    mod_type        TEXT,                        -- base | sauce | veggie | topping | chutney | main | other (from modifier_tags.csv)
    needs_review    BOOLEAN NOT NULL DEFAULT FALSE,  -- untagged / new modifiers flagged here
    first_seen      DATE,
    last_seen       DATE
);

CREATE TABLE IF NOT EXISTS public.dim_dining_option (
    dining_option   TEXT PRIMARY KEY,
    behavior        TEXT                         -- DINE_IN | TAKE_OUT | DELIVERY (from Config API)
);

CREATE TABLE IF NOT EXISTS public.dim_channel (
    channel_code    TEXT PRIMARY KEY,            -- IN_HOUSE | APP | TPD | CATERING | OFFSITE | OTHER
    display_name    TEXT NOT NULL
);
INSERT INTO public.dim_channel VALUES
    ('IN_HOUSE','In-House'),('APP','App'),('TPD','3PD Delivery'),
    ('CATERING','Catering'),('OFFSITE','Offsites'),('OTHER','Other')
ON CONFLICT DO NOTHING;

-- Manual channel corrections that must survive every refresh.
-- COALESCE'd over the default CASE logic at merge time.
CREATE TABLE IF NOT EXISTS public.channel_override (
    order_guid      TEXT PRIMARY KEY,
    channel_code    TEXT NOT NULL REFERENCES public.dim_channel(channel_code),
    set_by          TEXT,
    set_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    note            TEXT
);
