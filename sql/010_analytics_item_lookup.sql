CREATE TABLE IF NOT EXISTS analytics.item_lookup (
  raw_item_name      TEXT        NOT NULL,
  cleaned_item_name  TEXT        NOT NULL,
  category_1         TEXT,
  category_2         TEXT,
  loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (raw_item_name)
);
