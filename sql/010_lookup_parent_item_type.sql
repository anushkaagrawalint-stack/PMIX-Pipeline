CREATE SCHEMA IF NOT EXISTS lookup;

CREATE TABLE IF NOT EXISTS lookup.parent_item_type (
  parent_item  TEXT        NOT NULL,
  item_type    TEXT        NOT NULL,
  loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (parent_item, item_type)
);
