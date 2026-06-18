# RASA PMix Dashboard — Context Document

**Use this file to start a new Claude Code session in the `pmix-dashboard` repo.**

---

## 1. What This Dashboard Is

A **Product Mix / Menu Engineering dashboard** for **RASA**, a 5-location DC-area Indian restaurant group. It reads from a Neon Postgres database that is populated daily by a separate pipeline (`pmix-pipeline`). The dashboard must show sales, menu mix, and eventually cost/margin data per item, channel, location, and fiscal period.

---

## 2. Database

- **Provider:** Neon Postgres (serverless)
- **Connection:** `DATABASE_URL` in `.env.local` (same connection string as the pipeline)
- **Driver to use:** `@neondatabase/serverless` — the official Neon driver for Next.js/serverless
- **Never** expose `DATABASE_URL` to the browser — all queries go in Server Components or Route Handlers

---

## 3. Five Locations

| Code | Name | 
|---|---|
| `BALLPARK` | Ballpark (1247 First St SE) |
| `MVT` | Mount Vernon Triangle (485 K Street NW) |
| `NL` | National Landing (2200 Crystal Drive) |
| `MOSAIC` | Mosaic (2905 District Ave) |
| `ROCKVILLE` | Rockville (12033 Rockville Pike) |

---

## 4. Database Schema — All Tables

### 4a. `public` schema — Toast sales data (daily refresh via API)

**`public.fact_order_lines`** — spine of the data, one row per item per check
```
selection_guid    TEXT PK
order_guid        TEXT
check_guid        TEXT
location_code     TEXT          -- BALLPARK | MVT | NL | MOSAIC | ROCKVILLE
business_date     DATE
canonical_name    TEXT          -- cleaned item name
menu_name         TEXT
menu_group        TEXT
sales_category    TEXT
dining_option     TEXT
channel_code      TEXT          -- IN_HOUSE | APP | TPD | CATERING | OFFSITE | OTHER
quantity          NUMERIC
line_total        NUMERIC       -- gross sales, pre-tax, pre-discount, excl. service charges
pre_discount      NUMERIC
is_voided         BOOLEAN       -- ALWAYS filter WHERE NOT is_voided for revenue queries
is_deferred       BOOLEAN
```

**`public.fact_modifiers`** — one row per modifier at any nesting depth
```
modifier_guid     TEXT
parent_selection  TEXT          -- FK to fact_order_lines.selection_guid
location_code     TEXT
business_date     DATE
canonical_name    TEXT
mod_type          TEXT          -- base | sauce | veggie | topping | chutney | main | other
depth             INT
quantity          NUMERIC
price             NUMERIC
is_voided         BOOLEAN
```

**`public.fact_checks`** — check-level totals
```
check_guid        TEXT PK
order_guid        TEXT
location_code     TEXT
business_date     DATE
is_voided         BOOLEAN
tax_amount        NUMERIC
total_amount      NUMERIC
```

**`public.br_order_payment`** — payment method per check
```
payment_guid      TEXT PK
check_guid        TEXT
order_guid        TEXT
location_code     TEXT
business_date     DATE
payment_type      TEXT
alt_payment_name  TEXT          -- key for channel attribution (DoorDash, EzCater, etc.)
amount            NUMERIC
tip_amount        NUMERIC
```

**`public.fact_adjustments`** — discounts, comps, service charges
```
order_guid        TEXT
check_guid        TEXT
selection_guid    TEXT
location_code     TEXT
business_date     DATE
kind              TEXT
name              TEXT
amount            NUMERIC
```

**`public.dim_location`**
```
location_code   TEXT PK
toast_guid      TEXT
display_name    TEXT
```

**`public.dim_item`**
```
item_key        BIGINT PK
canonical_name  TEXT
menu_name       TEXT
menu_group      TEXT
sales_category  TEXT
first_seen      DATE
last_seen       DATE
```

**`public.dim_channel`**
```
channel_code    TEXT PK   -- IN_HOUSE | APP | TPD | CATERING | OFFSITE | OTHER
display_name    TEXT      -- In-House | App | 3PD Delivery | Catering | Offsites | Other
```

---

### 4b. `analytics` schema — lookup + cost tables (loaded from Excel files)

**`analytics.modifier_type`** — from LookupItemAndModifierType.xlsx
```
modifier_name   TEXT
item_type       TEXT
modifier_type   TEXT      -- Base | Sauce | Veggie | Main | etc.
loaded_at       TIMESTAMPTZ
PK: (modifier_name, item_type)
```

**`analytics.parent_item_type`** — from LookupItemAndModifierType.xlsx
```
parent_item     TEXT
item_type       TEXT
loaded_at       TIMESTAMPTZ
PK: (parent_item, item_type)
```

**`analytics.item_lookup`** — from LookupMenuBreakdown.xlsx (raw→cleaned name + categories)
```
raw_item_name      TEXT PK
cleaned_item_name  TEXT
category_1         TEXT      -- e.g. 'Bowls', 'Plates', '3PD MARKUP'
category_2         TEXT
loaded_at          TIMESTAMPTZ
```

**`analytics.r365_modifier_cost`** — R365 recipe costs per period
```
period            TEXT      -- e.g. 'P03-2026'
recipe_name       TEXT      -- e.g. 'MI Aloo Gobhi'
clean_name        TEXT      -- e.g. 'Aloo Gobhi'
portion_unit      TEXT      -- Each | Portion | CT | Inactive
cogs_account      TEXT
total_cost        NUMERIC(12,4)
cost_per_portion  NUMERIC(12,4)
PK: (period, recipe_name)
```

**`analytics.r365_item_cost`** — R365 item-level costs per period
```
period              TEXT      -- e.g. 'P03-2026'
menu                TEXT      -- DELIVERY | FOOD - IN HOUSE | APP | DRINKS - IN HOUSE
item_name           TEXT
item_name_updated   TEXT
menu_group          TEXT
category_1          TEXT
category_2          TEXT
avg_cost            NUMERIC(10,4)
PK: (period, menu, item_name)
```

**`public.fact_bikky_instore`** — Bikky in-store retention data (period grain)
**`public.fact_bikky_3pd_loyalty`** — Bikky 3PD + loyalty data (period grain)

---

### 4c. `raw` schema — immutable audit trail (don't query from dashboard)
`raw.toast_orders` (raw JSON), `raw.toast_config`, `raw.pull_runs`

---

## 5. Key Query Rules

```sql
-- ALWAYS add WHERE NOT is_voided for any revenue number
SELECT SUM(line_total) FROM public.fact_order_lines WHERE NOT is_voided;

-- Revenue = line_total (gross, pre-tax, pre-discount, excl. service charges)
-- This differs from Toast UI "Net Sales" by service charges + discounts — expected

-- Channel breakdown
SELECT channel_code, SUM(line_total) AS revenue
FROM public.fact_order_lines
WHERE NOT is_voided
  AND business_date BETWEEN '2026-02-01' AND '2026-06-11'
GROUP BY channel_code ORDER BY revenue DESC;

-- Top items
SELECT canonical_name, SUM(quantity) qty, ROUND(SUM(line_total),2) sales
FROM public.fact_order_lines
WHERE NOT is_voided
GROUP BY canonical_name ORDER BY sales DESC LIMIT 20;

-- Revenue by location
SELECT location_code, ROUND(SUM(line_total),2) revenue
FROM public.fact_order_lines
WHERE NOT is_voided
GROUP BY location_code ORDER BY revenue DESC;
```

---

## 6. Current Data in DB

- **Date range:** Feb 1 – Jun 11, 2026 (all 5 locations)
- **Volume:** ~93,000 orders, ~218,000 line items, ~$2.73M non-void sales
- **R365 costs:** P02–P05 2026 (modifier cost + item cost)
- **Bikky:** available per fiscal period (P01–P05 2026)
- **Pipeline runs daily** via GitHub Actions (`pmix-pipeline` repo)

---

## 7. Fiscal Periods

RASA uses a P1–P13 fiscal calendar. Each period ≈ 4 weeks.
- P2 2026 = February 2026
- P3 2026 = March 2026
- P4 2026 = April 2026
- P5 2026 = May 2026

---

## 8. Channels

| Code | Display | What it means |
|---|---|---|
| `IN_HOUSE` | In-House | Dine-in and takeout at the restaurant |
| `APP` | App | Toast Online / THANX loyalty app orders |
| `TPD` | 3PD Delivery | DoorDash, Uber Eats, GrubHub |
| `CATERING` | Catering | EzCater, HUNGRY, Sharebite, etc. |
| `OFFSITE` | Offsites | Aramark, Fooda, pop-up events |
| `OTHER` | Other | Unclassified |

---

## 9. Tech Stack for Dashboard

- **Framework:** Next.js (App Router, TypeScript)
- **DB driver:** `@neondatabase/serverless`
- **Charts:** Tremor or Recharts
- **Deployment:** Vercel (link Neon DB directly in Vercel dashboard)
- **All DB queries:** Server Components or Route Handlers only — never client-side

---

## 10. Phase 2 Analytics (not yet built — coming soon)

These will land in the `analytics` schema once the cost engine is built:
- `analytics.mv_menu_engineering` — Star/Plow Horse/Puzzle/Dog quadrants per item × channel × period
- `analytics.fact_retention` — Bikky return/reorder rates per item per period

The dashboard should be designed to accommodate these tables when ready.

---

## 11. Pipeline Repo

The data pipeline lives at `pmix-pipeline` (separate repo). It pulls from Toast API daily and loads all the tables above. Do not modify pipeline code from the dashboard repo.
