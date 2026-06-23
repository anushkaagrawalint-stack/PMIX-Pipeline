# RASA PMIX Dashboard — Full Engineering Prompt

---

## CONTEXT

Build a production-ready Product Mix (PMIX) dashboard for RASA (Indian restaurant chain, 5 locations) as a Next.js app. The backend is a Neon Postgres database already populated by a Toast POS pipeline. The UI reference is a static HTML prototype (`demofront.html`). The goal is to replace the manual Google Sheets PMIX process with a live, dynamic dashboard.

**Locations:** BALLPARK, MVT, MOSAIC, NL, ROCKVILLE

**Channels (EXPANDING — this is new):** Previously only IN_HOUSE, 3PD, LOYALTY. Now: IN_HOUSE, APP, TPD, CATERING, OFFSITE. All channel calculations are identical — the only difference is the Category/Sub-category hierarchy per channel (see below).

---

## DATABASE SCHEMA (Neon Postgres — read-only from the frontend)

```sql
-- Core fact table
public.fact_order_lines (
  selection_guid, order_guid, check_guid, location_code, business_date DATE,
  item_key, canonical_name,
  menu_name,        -- Toast menu (FOOD-IN-HOUSE, APP, DELIVERY, etc.)
  menu_group,       -- BOWLS, SIDES, DRINKS, PLATES, etc.
  sales_category,   -- from Toast sales categories
  dining_option,    -- Dine In / Open App - Takeout / Open App - Delivery
  channel_code,     -- IN_HOUSE | APP | TPD | CATERING | OFFSITE
  quantity, line_total, pre_discount,
  is_voided, is_deferred, pull_run_id
)

-- Item dimension
public.dim_item (
  natural_key, item_guid, canonical_name,
  menu_name, menu_group, sales_category, first_seen, last_seen
)

-- Modifiers (BYO components)
public.fact_modifiers (
  modifier_guid, parent_selection, order_guid, location_code, business_date,
  canonical_name, mod_type, depth, quantity, price, is_voided
)

-- Payments (source of category for CATERING/OFFSITE)
public.br_order_payment (
  payment_guid, check_guid, order_guid, location_code, business_date,
  payment_type, alt_payment_name,   -- EzCater / Fooda / Aramark / DoorDash etc.
  amount, tip_amount
)

-- Locations
public.dim_location (location_code, toast_guid, display_name)

-- Manual channel overrides
public.channel_override (order_guid, channel_code)

-- R365 costs (loaded via pipeline)
analytics.r365_item_cost (item_name, location, period, avg_cost)
analytics.r365_modifier_cost (modifier_name, modifier_type, period, avg_cost)

-- Lookup tables (category/sub-category mapping)
analytics.lookup_menu_breakdown (
  item_name, cleaned_item_name, category_1, category_2
  -- category_1 = Entrees / NA Drinks / Sides / Sweets / Kids / Retail / Alcohol
  -- category_2 = Bowl / Plates / Burrito / Lassi / Juice / Bread / etc.
)
analytics.lookup_item_modifier_type (
  item_name, item_type, modifier_type_new
  -- modifier types: Bases, Mains, Extra Mains, Veggies, Extra Veggies,
  --                 Sauces, Chutney and Dressings, Toppings, Make it a Meal
)
```

---

## OPEN ITEMS — SPECIAL CATEGORY

Items where `menu_group IS NULL OR menu_group = ''` AND `sales_category IS NOT NULL` are **Open Items** — they were rung in without a menu group assignment in Toast (common for custom/ad-hoc items, off-menu items, comp items, or setup errors).

These items must be:
1. **Flagged** in the Item Mix, All Items, and ME tabs with an "OPEN ITEM" badge
2. **Excluded from ME classification** (cannot be Star/Plow/Puzzle/Dog without a category)
3. **Tracked in the dedicated Open Items tab** (see Dashboard Tabs section)
4. **Category assigned** as `sales_category` value for display purposes when `category_1` from lookup is unavailable

Detection SQL:
```sql
WHERE (menu_group IS NULL OR menu_group = '')
  AND sales_category IS NOT NULL
  AND NOT is_voided
```

---

## CHANNEL → CATEGORY → SUB-CATEGORY HIERARCHY

Every view that shows "category" must respect this hierarchy:

| Channel | Category (level 2) | Sub-Category (level 3) |
|---|---|---|
| IN_HOUSE | `lookup_menu_breakdown.category_1` (Entrees, NA Drinks, Sides…) | `lookup_menu_breakdown.category_2` (Bowl, Plates, Burrito…) |
| APP | same as IN_HOUSE | same as IN_HOUSE |
| TPD | same as IN_HOUSE; note: 3PD cost = modifier_cost × 1.18 | same as IN_HOUSE |
| CATERING | `alt_payment_name` (EzCater, Hungry, Sharebite, Territory, Cater Cow, WCK, Food Fleet, ZeroCater, Cater2Me) | `lookup_menu_breakdown.category_1` |
| OFFSITE | `alt_payment_name` (Fooda, Aramark, Eurest, Metz, Taher, Foodworks, Cureate, Guest Services) | `lookup_menu_breakdown.category_1` |

**How to get alt_payment_name for an order:**
```sql
SELECT DISTINCT ON (order_guid)
  order_guid, alt_payment_name
FROM public.br_order_payment
WHERE alt_payment_name IS NOT NULL
ORDER BY order_guid, amount DESC
```

---

## CORE METRIC CALCULATIONS

These must be exact — no approximations.

```
avg_price             = SUM(line_total) / SUM(quantity)
                        WHERE NOT is_voided

avg_cost              = from analytics.r365_item_cost
                        matched by canonical_name + period

avg_cost_with_mods    = (total_avg_cost + total_modifier_cost) / quantity
                        -- see "Pink Sheet Logic" section below for exact formula

margin                = avg_price - avg_cost_with_mods
cogs_pct              = (avg_cost_with_mods * quantity) / net_sales
margin_pct            = margin / avg_price
net_sales             = SUM(line_total)           WHERE NOT is_voided
total_cost            = avg_cost_with_mods * quantity
total_margin          = net_sales - total_cost
menu_mix_pct          = item_quantity / SUM(all_quantities_in_scope)
sls_pct               = item_net_sales / SUM(all_net_sales_in_scope)

-- 3PD modifier cost uplift:
avg_cost_with_mods_tpd = avg_cost + (total_modifier_cost_online / quantity) * 1.18

-- Blended (across channels): WEIGHTED averages, not simple averages
blended_avg_price     = SUM(net_sales) / SUM(quantity)
blended_avg_cost      = SUM(total_cost) / SUM(quantity)
blended_margin_pct    = (blended_avg_price - blended_avg_cost) / blended_avg_price
```

---

## PINK SHEET LOGIC — avg_cost_with_mods per Entree

This is the core cost calculation derived from the item-level sheets in the PMIX Excel (the "pink sheets"). Each BYO / entree item has modifier costs computed by type and summed. This must be replicated exactly in the backend.

### Modifier types and their source

All modifier data comes from `public.fact_modifiers` joined to `analytics.r365_modifier_cost`.

```
Modifier types (from analytics.lookup_item_modifier_type.modifier_type_new):
  Bases
  Mains
  Extra Mains
  Veggies
  Extra Veggies
  Sauces
  Chutney and Dressings
  Toppings
  Make it a Meal   ← combines: Sides + Drinks + Sweets
```

### Per-type cost calculation

For each modifier type for a given item + period + channel scope:

```
type_total_cost = SUM(
  modifier_qty × r365_modifier_cost.avg_cost
)
-- where modifier_qty = SUM(fact_modifiers.quantity) grouped by modifier name
-- and   avg_cost     = analytics.r365_modifier_cost matched by modifier_name + modifier_type + period
```

### ½ and ½ item weighted average (critical edge case)

Modifiers named `"1/2 and 1/2 [type]"` (e.g. "1/2 and 1/2 Grains", "1/2 and 1/2 Mains") do NOT have a single cost. Their cost must be computed as a weighted average of the individual `"1/2 X"` modifiers chosen.

```
For a modifier named "1/2 and 1/2 Grains":

  half_mods = all modifiers named "1/2 [X]" under the same parent item
              (e.g. "1/2 Basmati Rice", "1/2 Lemon Turmeric Rice", "1/2 Masala Quinoa", ...)

  cost_of_half_and_half = SUM(qty_of_each_½_mod × cost_of_base_mod) / SUM(qty_of_all_½_mods)
  -- base_mod cost = cost of the full item (e.g. cost of "Basmati Rice", not "1/2 Basmati Rice")

  total_cost_for_half_and_half_rows = qty_of_"1/2 and 1/2 Grains" × cost_of_half_and_half
```

Same logic applies to `"1/2 and 1/2 Mains"`.

### Plates rule — Mains excluded

Items with `category_2 = 'Plates'` (Butter Chicken, Chicken Tikka Masala, Saag Paneer, etc.):
- **Do NOT include Mains or Extra Mains** in total modifier cost
- All other modifier types still apply

### Make it a Meal

Combine `mod_type IN ('Sides', 'Drinks', 'Sweets')` into one "Make it a Meal" bucket. Calculate and sum as a single modifier type cost.

### Channel scope for modifier cost

- **APP + TPD** (online channels): all modifier types apply — bases, mains, veggies, sauces, toppings, chutneys, make it a meal
- **IN_HOUSE**: only Mains logged in Toast. Bases/Sauce/Veggie/Topping/Chutney are NOT recorded in-house. So in-house `total_modifier_cost` = Mains + Extra Mains + Make it a Meal only
- **CATERING / OFFSITE**: treat same as IN_HOUSE for modifier cost calculation

### Final formula (matches Excel pink sheet exactly)

```
total_modifier_cost   = SUM of all per-type costs above (in total dollars, not per-unit)
total_avg_cost        = r365_item_cost.avg_cost × item_quantity_sold
modifier_plus_avg     = total_modifier_cost + total_avg_cost
final_avg_cost        = modifier_plus_avg / item_quantity_sold

-- 3PD uplift applied after:
final_avg_cost_tpd    = r365_item_cost.avg_cost + (total_modifier_cost / item_qty) * 1.18
```

This matches the Excel rows:
- `AVG COST OF [ITEM]` = r365_item_cost.avg_cost (base cost per unit)
- `TOTAL MODIFIER COST` = sum of all modifier type totals
- `TOTAL AVG COST` = r365 avg_cost × qty
- `MODIFIER + AVG COST` = TOTAL MODIFIER COST + TOTAL AVG COST
- `FINAL AVG COST WITH MODIFIER` = (MODIFIER + AVG COST) / qty_sold

### Items without modifier data (non-BYO)

Items like sides, drinks, retail, sweets — no BYO modifiers. For these:
```
avg_cost_with_mods = r365_item_cost.avg_cost   (no modifier uplift)
```

---

## MENU ENGINEERING CLASSIFICATION

Recalculates dynamically on every filter change (date, location, channel).

```
avg_margin_threshold  = weighted blended margin_pct across ALL items in scope
menu_mix_threshold    = (1 / count_of_unique_items_in_scope) * 0.7

Per item:
  margin_class = 'High' if item.margin_pct >= avg_margin_threshold else 'Low'
  mix_class    = 'High' if item.menu_mix_pct >= menu_mix_threshold else 'Low'

ME quadrant:
  Star       = High margin + High mix
  Plow Horse = Low margin  + High mix
  Puzzle     = High margin + Low mix
  Dog        = Low margin  + Low mix
```

Show live threshold values in the ME info banner:
`"Avg margin threshold: X% · Menu mix threshold: Y% ((1/N) × 0.7)"`

---

## PERIOD DEFINITIONS

RASA uses 4-week accounting periods. Store as constants:

```
P1 2026: Dec 31, 2025 – Jan 27, 2026
P2 2026: Jan 28     – Feb 24, 2026
P3 2026: Feb 25     – Mar 24, 2026
P4 2026: Mar 25     – Apr 22, 2026
P5 2026: Apr 23     – May 20, 2026
P6 2026: May 21     – Jun 17, 2026
P7 2026: Jun 18     – Jul 15, 2026
P8 2026: Jul 16     – Aug 12, 2026
```

Filter bar must support: This Week, Last Week, Last 4 Weeks, YTD, Q1–Q4, P1–P13, Custom date range.

---

## API ROUTES

All routes accept query params:
`?start=YYYY-MM-DD&end=YYYY-MM-DD&location=ALL|BALLPARK|MVT|MOSAIC|NL|ROCKVILLE&channel=ALL|IN_HOUSE|APP|TPD|CATERING|OFFSITE`

```
GET /api/summary
  → { items_sold, net_revenue, avg_margin_pct, unique_items, top_item_name,
      top_item_mix_pct, top_item_revenue }

GET /api/items
  → [ { canonical_name, category_1, category_2, menu_group,
        quantity, net_sales, avg_price, avg_cost_with_mods,
        margin_pct, cogs_pct, total_margin, menu_mix_pct, sls_pct,
        me_quadrant, margin_class, mix_class } ]

GET /api/channels
  → { by_channel: [ { channel_code, quantity, net_sales, pct_of_total } ],
      catering_categories: [ { alt_payment_name, orders, net_sales, aov, pct } ],
      offsite_categories:  [ { alt_payment_name, orders, net_sales, aov, pct } ],
      tpd_categories:      [ { alt_payment_name, orders, net_sales, aov, pct } ] }

GET /api/locations
  → [ { location_code, item_name, quantity, net_sales, menu_mix_pct } ]
     pivoted so frontend renders location × item matrix

GET /api/byo?item=GRAIN_BOWL|ALL
  → { mains, extra_mains, bases, sauces, veggies, toppings, chutneys }
     each: [ { modifier_name, quantity, pct_of_total } ]

GET /api/payments
  → [ { source_name, channel_category, payment_count,
        net_revenue, avg_ticket, pct_of_total } ]

GET /api/me
  → { thresholds: { avg_margin_pct, menu_mix_pct, item_count },
      quadrants:  { stars: N, plow_horses: N, puzzles: N, dogs: N },
      items:      [ ...full item list with ME classification ] }

GET /api/renames
  → [ { canonical_name, all_names: string[], lifetime_qty,
        lifetime_sales, location_count, first_seen } ]

GET /api/needs-review
  → [ { order_guid, location_code, business_date, amount,
        issue_type, current_channel, dining_option,
        alt_payment_name, suggested_channel } ]

GET /api/bikky
  → [ { canonical_name, category_1, net_sales, quantity, guests,
        return_rate, reorder_rate, vs_prior_period_delta } ]

GET /api/open-items
  → { summary: { total, revenue_affected, missing_cost_count, uncategorized_count },
      items: [ { canonical_name, sales_category, menu_group, channel_code,
                 issue_types: string[], quantity, net_sales, last_seen,
                 suggested_fix, raw_values } ] }
```

---

## DASHBOARD TABS

Match `demofront.html` structure exactly.

### 1. Overview
- 5 KPI cards: Items Sold, Net Revenue, Avg Margin %, Unique Items, Top Item
- Weekly sales trend: bar (revenue) + line (qty) on dual Y-axis
- Revenue by channel: donut chart
- Top 8 items by revenue: horizontal bar
- Revenue by category (category_1): horizontal bar

### 2. Item Mix
- Search input + GROUP BY selector (Menu Group / Category / Menu) + toggle (Qty / Revenue / % Mix)
- Grouped collapsible table: group header row shows group totals
- Columns: Item, Menu, Menu Group, Qty, Revenue, Avg Price, % Mix, Orders

### 3. Location Compare

UI layout matches the reference screenshot exactly:

**Top — Location cards (one per location, horizontal row)**
Each card shows:
- Location name (colored label: BALLPARK red, MVT amber, MOSAIC green, NL blue, ROCKVILLE purple)
- Primary metric value (Revenue $ / Qty / % Mix depending on toggle)
- Items sold count
- % of group revenue
- Event label tag if applicable (e.g. "Spring Fest-MVT", "Holli Event")

**Filter bar (inside this tab)**
- `All Locations ▾` dropdown (filter to one)
- `Revenue ▾` toggle (Revenue / Quantity / % Mix) — changes all cards + charts + table

**Middle row — two panels side by side**
Left panel — **Revenue by Location** (vertical bar chart, one bar per location, location colors)
Right panel — **Category Mix by Location (% of Revenue)** — matrix table:
- Rows = category_1 values (Entrees, NA Drinks, Sides, Sweets, Kids, etc.)
- Columns = CATEGORY | BALLPARK | MVT | MOSAIC | NL | ROCKVILLE
- Values = pct of that location's total revenue

**Bottom — Item Comparison table**
- Header: "ITEM COMPARISON — REVENUE" (or Quantity / % Mix per toggle)
- Controls: Search items input, `Top 20 ▾` dropdown (Top 10 / Top 20 / Top 50 / All), `SORT BY Avg ▾`, `High → Low ▾`
- Columns: ITEM | BALLPARK | MVT | MOSAIC | NL | ROCKVILLE | AVG ↓
- Each cell shows the metric value; highest value per row highlighted in bold purple
- `—` shown when item not sold at that location in the period

### 4. Channel & Menu
Three sections:

**Channel overview**
- Top items per channel (2-column list)
- Revenue by Toast menu (horizontal bar)
- Channel delta table: IH % vs other channel %, Δ pp with color tags

**Catering deep-dive**
- KPIs: Catering Revenue, Catering Orders, People Served, Avg Party Size, % of Total Rev
- Platform breakdown table: alt_payment_name, Orders, Revenue, AOV, % of Catering
- Top catering items table

**Offsites deep-dive**
- KPIs: Offsite Revenue, Orders, Avg Order Value, % of Total Rev
- Vendor breakdown table: alt_payment_name, Orders, Revenue, AOV, % of Offsite
- Day-of-week pattern table

**TPD deep-dive** *(new)*
- KPIs: TPD Revenue, Orders, Avg Order Value, % of Total Rev
- Platform breakdown: DoorDash, Uber Eats, GrubHub, Postmates
- Top TPD items table
- Note: display costs with 1.18× modifier markup

### 5. BYO Breakdown
- Item selector (All BYO / Grain Bowl / Greens+Grains / Salad Bowl / Burrito)
- Channel filter (All / In-House / App / 3PD)
- Info banner: "Mains logged on every order. Bases/Sauce/Veggie/Topping/Chutney are online-only (App + 3PD)."
- Bar lists for: Most popular main, Extra mains, Base, Sauce, Veggie, Toppings, Chutney+Dressing

### 6. Payment Source
- KPI cards: Alt Payment total, Card total, Gift Card total
- Stacked bar by location (Card / Alt Payment / Cash)
- Top payment sources horizontal bar
- Full table: Source name, Category (CARD / ALT_PAYMENT), Payments, Revenue, Avg Ticket, % Mix, share bar

### 7. Renames Audit
- Info banner: count of canonical groups with multiple historical names
- Table: Canonical name, Category, All historical names (strikethrough old), Lifetime Qty, Lifetime $, Locations, First seen

### 8. Needs Review
- Warning banner: count of orders needing channel decision
- Per-order cards: issue type tag, location/date/amount, current channel, dining option, alt_payment_name, suggested fix
- Inline override: dropdown + Set button (calls PATCH `/api/channel-override`)
- "Apply all" button

### 9. Menu Engineering
- 4 quadrant summary cards: Stars (green), Plow Horses (purple), Puzzles (blue), Dogs (red) — count each
- Info banner with live thresholds
- Revenue by ME quadrant: donut chart
- ME scatter plot: X = menu mix rank, Y = margin %, bubble size = revenue
- Full item table: Item, ME badge, Category, Net Sales, Qty, Margin%, COGS%, Avg Price, Avg Cost
  - Filterable by quadrant dropdown + search

### 10. Channels
- Channel KPI row (one card per channel: Revenue, % of total, Δ vs prior period)
- Category breakdown bar charts:
  - IN_HOUSE: by category_1
  - TPD: by category_1
  - APP: by category_1
  - CATERING: by alt_payment_name (vendor)
  - OFFSITE: by alt_payment_name (vendor)
- Item-level channel split table: Item, ME badge, Total Sales, per-channel $ columns, stacked mini-bar

### 11. Customer Retention (Bikky)
- Info banner: data source + 90-day window note
- KPI cards: Avg Return Rate, Avg Reorder Rate, Top retention item, Lowest retention item
- Table: Item, Category, Revenue, Qty, Guests, Return Rate (color-tagged), Reorder Rate (color-tagged), Δ vs prior period

### 12. Open Items

Items with `menu_group IS NULL` but `sales_category IS NOT NULL` — these need attention (missing category mapping, off-menu items, setup errors).

**Top — Summary KPIs (4 cards)**
- Total open items count
- Revenue affected (sum of line_total for open items)
- Count: Missing cost
- Count: Uncategorized (not in lookup_menu_breakdown)

**Filter bar**
- Search by item name
- Filter by issue type: ALL / NO COST / UNCATEGORIZED / WRONG CHANNEL / MISSING MENU GROUP
- Date range / period picker
- Location filter

**Main table**
Columns: Item Name | Issue Type badge | Sales Category | Menu Group | Channel | Qty Sold | Revenue | Last Seen | Action

Issue type badges:
- `NO COST` (amber) — canonical_name not found in r365_item_cost for this period
- `UNCATEGORIZED` (blue) — not in lookup_menu_breakdown
- `MISSING MENU GROUP` (red) — menu_group is null/blank
- `WRONG CHANNEL` (purple) — channel_code conflicts with payment type

**Per-row expandable detail**
Shows raw DB values:
- `menu_name` / `menu_group` / `sales_category` / `channel_code` / `dining_option`
- `alt_payment_name` if present
- Suggested fix hint (e.g. "Add to lookup_menu_breakdown with category: Entrees / Plates")
- Link to matching lookup entry if fuzzy match found

**Bottom**
- Pagination (25 per page)
- "Bulk apply suggestions" button → writes to lookup tables or channel_override

**API route:**
```
GET /api/open-items
  → { summary: { total, revenue_affected, missing_cost, uncategorized },
      items: [ { canonical_name, sales_category, menu_group, channel_code,
                 issue_types: string[], quantity, net_sales, last_seen,
                 suggested_fix, raw_values } ] }
```

### 13. All Items
- Full detail table: Item, ME badge, Category, Sub-Category, Net Sales, Qty, Margin%, COGS%, Avg Price, Avg Cost, Total Margin, Mix%
- Sortable columns (click header)
- Filter by ME quadrant dropdown + search
- Export CSV button

---

## FILTER BAR (global — affects all tabs)

```
[Date dropdown]  [Location: All ▾]  [Menu: All menus ▾]  [☑ Real menu items only]
[Channel: All] [In-House] [App] [3PD] [Catering] [Offsites]
```

- **Date dropdown:** quick selects + Q1–Q4 + P1–P13 + custom range picker with Apply button
- **Location:** single-select (All / BALLPARK / MVT / MOSAIC / NL / ROCKVILLE)
- **Menu:** multi-select Toast menus (FOOD-IN-HOUSE, APP, DELIVERY, CATERING, etc.)
- **Real menu items only:** excludes modifiers, combos, items not in `lookup_menu_breakdown`
- **Channel pills:** multi-select toggle; "All" deselects others

---

## VOIDS & DEFERRED RULES

- All revenue/quantity metrics: `WHERE NOT is_voided AND NOT is_deferred`
- Voided items preserved in DB — visible in audit/needs-review tabs
- Show void counts in audit tabs for transparency

---

## COST MATCHING LOGIC

```
1. Match: canonical_name + period (e.g. 'P5 2026') + location_code
2. Fallback: canonical_name + period (company-wide, no location filter)
3. Fallback: canonical_name, most recent period available
4. No cost found: show item with NULL cost, flag with "cost not set" warning icon
5. 3PD modifier cost = online modifier cost × 1.18
```

---

## DATA FRESHNESS

- Header badge: "Last data: YYYY-MM-DD" from `SELECT MAX(business_date) FROM public.fact_order_lines`
- Refresh button: invalidates all React Query cache keys
- React Query stale time: 5 minutes

---

## TECH STACK

| Layer | Choice |
|---|---|
| Framework | Next.js 14 App Router, TypeScript |
| Styling | Tailwind CSS (match demofront.html color variables exactly) |
| Charts | Chart.js 4 — bar, line, doughnut, bubble |
| Data fetching | TanStack React Query v5 |
| DB client | `@neondatabase/serverless` or `postgres` npm package |
| Auth | None for MVP |
| Font | Montserrat (Google Fonts) |

**Color variables (from demofront.html — use exactly):**
```css
--ch-inhouse:  #9f7cef
--ch-app:      #7cb9ef
--ch-3pd:      #ef7ccf
--ch-catering: #f5a623
--ch-offsite:  #2ec4b6
--loc-ballpark:#ef4444
--loc-mvt:     #f59e0b
--loc-mosaic:  #10b981
--loc-nl:      #3b82f6
--loc-rockville:#8b5cf6
--header:      #381d7c
--accent:      #7c3aed
```

---

## IMPORTANT EDGE CASES

1. **Channel attribution:** Use `channel_code` column directly from `fact_order_lines` — do NOT re-derive from `menu_name`. The pipeline sets this via `dining_option ILIKE '%open app%'` → APP, alt_payment matching → CATERING/OFFSITE/TPD, else IN_HOUSE.

2. **CATERING/OFFSITE category:** Comes from `alt_payment_name` in `br_order_payment`, not in `fact_order_lines`. Always join via `order_guid`.

3. **Blended weighted averages:** avg_price, avg_cost, margin_pct across channels = weighted by quantity. Never simple averages of per-channel values.

4. **BYO modifier grain:** Modifiers link to parent via `fact_modifiers.parent_selection = fact_order_lines.selection_guid`. For BYO analysis filter: `mod_type IN ('Mains','Bases','Sauces','Veggies','Toppings','Chutney and Dressings','Extra Mains','Extra Veggies','Make it a Meal')`.

5. **Menu mix denominator:** = SUM(quantity) of all non-voided, non-deferred, non-blocklisted items IN CURRENT FILTER SCOPE. Recalculates when any filter changes.

6. **ME threshold recalculation:** Must rerun on every filter change. Thresholds shown live in UI.

7. **Multi-location items:** Same canonical_name appears at multiple locations. Menu mix % by location = item qty at that location / total qty at that location (not company-wide total).

8. **Period cost lookup for R365:** `analytics.r365_item_cost.period` stores period codes like `'P5 2026'`. Map selected date range to the overlapping period(s) when querying costs.

---

## BIKKY DATA TABLES

```sql
public.fact_bikky_instore (
  item_name, location_code, period,
  net_sales, quantity, guests,
  return_rate, reorder_rate
)
public.fact_bikky_3pd_loyalty (
  item_name, location_code, period,
  net_sales, quantity, guests,
  return_rate, reorder_rate
)
```
Join to `fact_order_lines` by `canonical_name` (after normalize). Delta vs prior period = current period rate - previous period rate.

---

## ENV VARS REQUIRED

```
DATABASE_URL=postgresql://...@...neon.tech/neondb?sslmode=require
```
