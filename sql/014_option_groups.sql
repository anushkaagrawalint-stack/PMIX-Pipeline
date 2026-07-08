-- 014_option_groups.sql — add option_group columns (additive, nullable, safe)
ALTER TABLE staging.modifiers
    ADD COLUMN IF NOT EXISTS option_group_guid TEXT,
    ADD COLUMN IF NOT EXISTS option_group_name TEXT;

ALTER TABLE public.fact_modifiers
    ADD COLUMN IF NOT EXISTS option_group_guid TEXT,
    ADD COLUMN IF NOT EXISTS option_group_name TEXT;
