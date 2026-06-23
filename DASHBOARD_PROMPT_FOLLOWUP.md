# RASA PMIX Dashboard — Follow-up Corrections & Additions

This is a follow-up to the main dashboard prompt. It adds three things that were missing:
1. Pink Sheet logic — the exact `avg_cost_with_mods` calculation for entrees
2. Open Items — a special item category and dedicated tab
3. Location Compare — correct UI layout

Apply all of these on top of what was already specified.

---

## 1. PINK SHEET LOGIC — avg_cost_with_mods for Entrees

The `avg_cost_with_mods` value shown in the PMIX Excel is NOT simply `r365_item_cost.avg_cost`. It is computed per-item from modifier-level cost data. This must be replicated exactly in the backend.

### Modifier types

From `analytics.lookup_item_modifier_type.modifier_type_new`:

```
Bases
Mains
Extra Mains
Veggies
Extra Veggies
Sauces
Chutney and Dressings
Toppings
Make it a Meal    ← combines Sides + Drinks + Sweets modifiers into one bucket
```

### Per-type cost calculation

For each modifier type, for a given item + period + channel scope:

```
type_total_cost = SUM(modifier_qty × r365_unit_cost)

where:
  modifier_qty  = SUM(fact_modifiers.quantity) grouped by modifier name
                  filtered to parent item via fact_modifiers.parent_selection = fact_order_lines.selection_guid
  r365_unit_cost = analytics.r365_modifier_cost.avg_cost
                   matched by modifier_name + modifier_type + period
```

### ½ and ½ weighted average — critical edge case

Modifiers named `"1/2 and 1/2 [type]"` (e.g. "1/2 and 1/2 Grains", "1/2 and 1/2 Mains") have NO single cost. Compute as a weighted average of the individual `"1/2 X"` modifiers:

```
half_mods = all modifiers named "1/2 [X]" under the same parent item and modifier type
            e.g. "1/2 Basmati Rice", "1/2 Lemon Turmeric Rice", "1/2 Masala Quinoa" ...

cost_of_half_and_half = SUM(qty_of_½_X × cost_of_full_X) / SUM(qty_of_all_½_X_mods)
-- use cost of the FULL version (e.g. cost of "Basmati Rice", not "1/2 Basmati Rice")

total_cost_for_½_and_½_rows = qty_of_"1/2 and 1/2 [type]" rows × cost_of_half_and_half
```

Same logic for `"1/2 and 1/2 Mains"`.

### Plates rule

Items with `category_2 = 'Plates'` (Butter Chicken, Chicken Tikka Masala, Saag Paneer, Paneer Tikka Masala, etc.):
- **Exclude Mains and Extra Mains** from total modifier cost
- All other modifier types still apply

### Channel scope for modifier recording

Toast only records certain modifier types per channel:

| Channel | Modifier types recorded in Toast |
|---|---|
| APP + TPD | All types: Bases, Mains, Extra Mains, Veggies, Extra Veggies, Sauces, Toppings, Chutneys, Make it a Meal |
| IN_HOUSE | Mains only (bases/sauce/veggie/topping/chutney not entered at counter) |
| CATERING / OFFSITE | Same as IN_HOUSE |

So:
- `total_modifier_cost` for APP/TPD = full sum of all types
- `total_modifier_cost` for IN_HOUSE/CATERING/OFFSITE = Mains + Extra Mains + Make it a Meal only

### Final formula (exact match to Excel pink sheet)

```
total_modifier_cost  = SUM of all per-type totals above (total dollars across all orders)
total_avg_cost       = r365_item_cost.avg_cost × item_quantity_sold
modifier_plus_avg    = total_modifier_cost + total_avg_cost
final_avg_cost       = modifier_plus_avg / item_quantity_sold

-- TPD version:
final_avg_cost_tpd   = r365_item_cost.avg_cost + (total_modifier_cost / item_qty) * 1.18
```

Maps to these exact rows in the Excel pink sheet:
- `AVG COST OF [ITEM]` → `r365_item_cost.avg_cost`
- `TOTAL MODIFIER COST` → `total_modifier_cost`
- `TOTAL AVG COST` → `r365_item_cost.avg_cost × qty`
- `MODIFIER + AVG COST` → `total_modifier_cost + total_avg_cost`
- `FINAL AVG COST WITH MODIFIER` → `modifier_plus_avg / qty`

### Non-BYO items (sides, drinks, retail, sweets)

No modifier pivots exist for these. Use:
```
avg_cost_with_mods = r365_item_cost.avg_cost   (no modifier uplift)
```

---

## 2. OPEN ITEMS — Special Category & Tab

**Definition:** Items where `menu_group IS NULL OR menu_group = ''` AND `sales_category IS NOT NULL`. These are rung into Toast without a menu group (ad-hoc items, off-menu, comp items, setup errors).

### Rules across all tabs

- Flag with `OPEN ITEM` badge in Item Mix, All Items, and ME tabs
- **Exclude from ME classification** — cannot be Star/Plow/Puzzle/Dog without a category
- Use `sales_category` as the fallback display category when `lookup_menu_breakdown.category_1` is unavailable

**Detection:**
```sql
WHERE (menu_group IS NULL OR menu_group = '')
  AND sales_category IS NOT NULL
  AND NOT is_voided
```

### Dedicated Open Items tab

**Top — 4 KPI cards**
- Total open items (unique canonical_name count)
- Revenue affected (`SUM(line_total)` for open items)
- Missing cost count (not in `r365_item_cost` for current period)
- Uncategorized count (not in `lookup_menu_breakdown`)

**Filter bar**
- Search by item name
- Issue type: ALL / NO COST / UNCATEGORIZED / MISSING MENU GROUP / WRONG CHANNEL
- Date range / period
- Location

**Main table**

| Column | Source |
|---|---|
| Item Name | `canonical_name` |
| Issue Type badge | computed (see badges below) |
| Sales Category | `sales_category` |
| Menu Group | `menu_group` (blank/null) |
| Channel | `channel_code` |
| Qty Sold | `SUM(quantity)` |
| Revenue | `SUM(line_total)` |
| Last Seen | `MAX(business_date)` |
| Action | Resolve / Ignore button |

Issue badges:
- `NO COST` amber — not in `r365_item_cost` for this period
- `UNCATEGORIZED` blue — not in `lookup_menu_breakdown`
- `MISSING MENU GROUP` red — `menu_group` is null/blank
- `WRONG CHANNEL` purple — `channel_code` conflicts with `alt_payment_name`

**Per-row expandable detail**

Raw DB values: `menu_name`, `menu_group`, `sales_category`, `channel_code`, `dining_option`, `alt_payment_name`

Suggested fix hint, e.g.:
> "Add to lookup_menu_breakdown: category_1 = Entrees, category_2 = Plates"

**Bottom**
- 25 rows per page
- "Bulk apply suggestions" → writes to lookup tables or `channel_override`

**API:**
```
GET /api/open-items
  → { summary: { total, revenue_affected, missing_cost_count, uncategorized_count },
      items: [ { canonical_name, sales_category, menu_group, channel_code,
                 issue_types: string[], quantity, net_sales, last_seen,
                 suggested_fix, raw_values } ] }
```

---

## 3. LOCATION COMPARE — Correct UI Layout

Replace the simple location pills + matrix table with this layout (matches reference screenshot):

### Top — Location cards (horizontal row, one per location)

Each card:
- Location name as colored label (BALLPARK red `#ef4444`, MVT amber `#f59e0b`, MOSAIC green `#10b981`, NL blue `#3b82f6`, ROCKVILLE purple `#8b5cf6`)
- Primary metric value large (Revenue `$` / Qty / % Mix — controlled by toggle)
- Sub-line: "X items sold"
- Sub-line: "Y% of group revenue"
- Optional event tag (e.g. "★ Spring Fest-MVT") — sourced from a config/notes field

### Tab-level filter bar

```
[All Locations ▾]    [Revenue ▾]
```
- `All Locations ▾` — filter cards/charts/table to one location
- `Revenue ▾` — toggle between Revenue / Quantity / % Mix; updates all three sections below

### Middle row — two panels side by side

**Left: Revenue by Location** (vertical bar chart)
- One bar per location, colored by location color
- Y-axis: revenue `$K`
- X-axis: location names

**Right: Category Mix by Location (% of Revenue)** (matrix table, NOT a chart)
- Rows = `category_1` values (Entrees, NA Drinks, Sides, Sweets, Kids, Retail, Alcohol)
- Columns = CATEGORY | BALLPARK | MVT | MOSAIC | NL | ROCKVILLE
- Values = `item_net_sales_at_location / total_net_sales_at_location` as `%`
- Column headers use location colors

### Bottom — Item Comparison table

Header: `ITEM COMPARISON — REVENUE` (or QUANTITY / % MIX per toggle)

Controls (top-right of table):
- Search items input
- `Top 20 ▾` dropdown — Top 10 / Top 20 / Top 50 / All
- `SORT BY Avg ▾` — sort column selector
- `High → Low ▾` — direction toggle

Table columns: `ITEM | BALLPARK | MVT | MOSAIC | NL | ROCKVILLE | AVG ↓`

Rules:
- Highest value per row → bold, purple text (`#7c3aed`)
- Location not selling that item in period → show `—`
- Default sort: by AVG descending
- Location column headers use location colors

**API:**
```
GET /api/locations
  → {
      cards: [ { location_code, revenue, quantity, menu_mix_pct,
                 items_sold, pct_of_group, event_label } ],
      category_mix: [ { category_1, ballpark_pct, mvt_pct, mosaic_pct, nl_pct, rockville_pct } ],
      item_comparison: [ { canonical_name, ballpark, mvt, mosaic, nl, rockville, avg } ]
    }
```
