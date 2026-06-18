CREATE TABLE IF NOT EXISTS analytics.r365_modifier_cost (
  period            TEXT        NOT NULL,   -- e.g. 'P03-2026'
  recipe_name       TEXT        NOT NULL,
  clean_name        TEXT,
  portion_unit      TEXT,
  cogs_account      TEXT,
  total_cost        NUMERIC(12,4),
  cost_per_portion  NUMERIC(12,4),
  loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (period, recipe_name)
);
