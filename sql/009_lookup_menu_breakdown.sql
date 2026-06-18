CREATE SCHEMA IF NOT EXISTS lookup;

CREATE TABLE IF NOT EXISTS lookup.menu_breakdown (
  cleaned_item_name  TEXT        NOT NULL,
  category_1         TEXT,
  category_2         TEXT,
  loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (cleaned_item_name)
);
