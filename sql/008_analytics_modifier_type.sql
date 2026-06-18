CREATE TABLE IF NOT EXISTS analytics.modifier_type (
  modifier_name  TEXT        NOT NULL,
  item_type      TEXT        NOT NULL,
  modifier_type  TEXT,
  loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (modifier_name, item_type)
);
