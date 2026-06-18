CREATE SCHEMA IF NOT EXISTS lookup;

CREATE TABLE IF NOT EXISTS lookup.item_name_map (
  raw_item_name      TEXT        NOT NULL,
  cleaned_item_name  TEXT        NOT NULL,
  loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (raw_item_name)
);
