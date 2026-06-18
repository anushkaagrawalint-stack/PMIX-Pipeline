CREATE TABLE IF NOT EXISTS analytics.r365_item_cost (
  period              TEXT        NOT NULL,   -- e.g. 'P03-2026'
  menu                TEXT        NOT NULL,
  item_name           TEXT        NOT NULL,
  item_name_updated   TEXT,
  menu_group          TEXT,
  category_1          TEXT,
  category_2          TEXT,
  avg_cost            NUMERIC(10,4),
  loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (period, menu, item_name)
);
