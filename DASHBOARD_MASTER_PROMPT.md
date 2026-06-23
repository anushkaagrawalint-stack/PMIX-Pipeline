# RASA PMIX Dashboard — Master Build Prompt

**Single document. Use this alone. Supersedes all other prompts.**

Build a production Next.js dashboard that replicates the RASA PMIX Google Sheets process live from a Neon Postgres database. The static HTML prototype is `demofront.html` — match its visual design exactly. All business logic is extracted directly from the production Google Apps Script (`PMIX_AppScript.txt`) and the PMIX SOP.

---

## 1. TECH STACK

| Layer | Choice |
|---|---|
| Framework | Next.js 14 App Router, TypeScript |
| Styling | Tailwind CSS |
| Charts | Chart.js 4 (bar, line, doughnut, bubble) |
| Data fetching | TanStack React Query v5 |
| DB client | `@neondatabase/serverless` |
| Font | Montserrat (Google Fonts) |
| Auth | None (internal tool) |

**CSS color variables — use exactly as in `demofront.html`:**
```css
--bg: #f4f1f8
--card: #fff
--header: #381d7c
--accent: #7c3aed
--ch-inhouse: #9f7cef
--ch-app: #7cb9ef
--ch-3pd: #ef7ccf
--ch-catering: #f5a623
--ch-offsite: #2ec4b6
--loc-ballpark: #ef4444
--loc-mvt: #f59e0b
--loc-mosaic: #10b981
--loc-nl: #3b82f6
--loc-rockville: #8b5cf6
```

---

## 2. DATABASE SCHEMA

```sql
-- All pipeline data
public.fact_order_lines (
  selection_guid TEXT PRIMARY KEY,
  order_guid TEXT, check_guid TEXT, location_code TEXT,
  business_date DATE,
  item_key TEXT, canonical_name TEXT,
  menu_name TEXT,     -- e.g. FOOD-IN-HOUSE / DELIVERY / APP / OFFSITE POP-UPS / CATERING
  menu_group TEXT,    -- e.g. BOWLS / PLATES / SIDES / DRINKS / BURRITOS / KIDS
                      -- For OFFSITE POP-UPS: menu_group IS the category (Aramark / Fooda / etc.)
  sales_category TEXT,
  dining_option TEXT,
  channel_code TEXT,  -- IN_HOUSE | APP | TPD | CATERING | OFFSITE
  quantity NUMERIC, line_total NUMERIC, pre_discount NUMERIC,
  is_voided BOOLEAN, is_deferred BOOLEAN, pull_run_id TEXT
)

public.fact_modifiers (
  modifier_guid TEXT, parent_selection TEXT, order_guid TEXT,
  location_code TEXT, business_date DATE, canonical_name TEXT,
  mod_type TEXT,  -- Base | Main | Extra Main | Veggie | Extra Veggie |
                  -- Sauce | Chutney and Dressings | Topping | Make it a Meal
  depth INT, quantity NUMERIC, price NUMERIC, is_voided BOOLEAN
)

public.br_order_payment (
  payment_guid TEXT, check_guid TEXT, order_guid TEXT,
  location_code TEXT, business_date DATE,
  payment_type TEXT, alt_payment_name TEXT,
  amount NUMERIC, tip_amount NUMERIC
  -- alt_payment_name = EzCater / Fooda / Aramark / DoorDash / Uber / GrubHub / etc.
)

public.dim_item (natural_key TEXT, item_guid TEXT, canonical_name TEXT,
                 menu_name TEXT, menu_group TEXT, sales_category TEXT,
                 first_seen DATE, last_seen DATE)
public.dim_location (location_code TEXT, toast_guid TEXT, display_name TEXT)
public.channel_override (order_guid TEXT, channel_code TEXT)
public.fact_checks (check_guid TEXT, order_guid TEXT, location_code TEXT,
                    business_date DATE, is_voided BOOLEAN,
                    tax_amount NUMERIC, total_amount NUMERIC)
public.fact_bikky_instore (item_name TEXT, location_code TEXT, period TEXT,
                            net_sales NUMERIC, quantity NUMERIC, guests INT,
                            return_rate NUMERIC, reorder_rate NUMERIC)
public.fact_bikky_3pd_loyalty (item_name TEXT, location_code TEXT, period TEXT,
                                net_sales NUMERIC, quantity NUMERIC, guests INT,
                                return_rate NUMERIC, reorder_rate NUMERIC)

-- Lookup & cost tables
analytics.item_lookup (raw_item_name TEXT, cleaned_item_name TEXT,
                        category_1 TEXT,   -- equals cleaned_item_name in most rows, NOT a category label
                        category_2 TEXT,   -- sub-type: Bowls / Plates / Burritos / Lassi / Bread / etc.
                        loaded_at TIMESTAMPTZ)
analytics.modifier_type (modifier_name TEXT, item_type TEXT, modifier_type TEXT, loaded_at TIMESTAMPTZ)
analytics.r365_item_cost (item_name TEXT, location TEXT, period TEXT, avg_cost NUMERIC)
analytics.r365_modifier_cost (modifier_name TEXT, modifier_type TEXT, period TEXT, avg_cost NUMERIC)
```

**Important note on `analytics.item_lookup.category_1`:** This field is NOT "Entrees" / "NA Drinks" / etc. It equals `cleaned_item_name` in most rows. The actual display category must be derived from `category_2` via the mapping in Section 4.

---

## 3. TOTAL QUANTITY & REVENUE INCLUSION RULES

These rules apply to ALL revenue, quantity, and mix calculations across every tab.

```
Rule A — Include:   NOT is_voided AND NOT is_deferred
Rule B — Include:   menu_group IS NOT NULL AND menu_group != ''
                    (normal items with a known menu group)
Rule C — Include:   menu_group IS NULL OR menu_group = ''
                    AND sales_category IS NOT NULL AND sales_category != ''
                    → These are OPEN ITEMS — include in totals but flag them.
                      They show up in total qty, total revenue, category totals.
                      They are EXCLUDED from ME classification.
Rule D — Exclude:   menu_group IS NULL OR menu_group = ''
                    AND (sales_category IS NULL OR sales_category = '')
                    → Ghost/misconfigured rows — no category and no sales_category.
                      Completely exclude from all calculations.
```

In SQL, the base filter for all queries is:
```sql
WHERE NOT is_voided
  AND NOT is_deferred
  AND NOT (
    (menu_group IS NULL OR menu_group = '')
    AND (sales_category IS NULL OR sales_category = '')
  )
```

The `EXCLUDED_GROUPS` / `EXCLUDED_MENUS` filter (toggled by "Real menu items only") applies on top of the above.

---

## 4. CHANNEL ATTRIBUTION

Channel is already resolved in `fact_order_lines.channel_code`. Never re-derive it. The pipeline sets it as:

```
dining_option ILIKE '%open app%'               → APP
alt_payment ILIKE '%doordash%|%uber%|...'      → TPD
alt_payment ILIKE '%ezcater%|%hungry%|...'     → CATERING
alt_payment ILIKE '%fooda%|%aramark%|...'      → OFFSITE
else                                            → IN_HOUSE
```

Channel filter pills: **All | In-House | App | 3PD | Catering | Offsite**

---

## 5. CATEGORY & SUB-CATEGORY RESOLUTION

Implement as constants in `/lib/categories.ts`.

### 5a. Standard channels (IN_HOUSE / APP / TPD)

```typescript
// From AppScript: GRP_TO_CATEGORY
export const GRP_TO_CATEGORY: Record<string, string> = {
  'BOWLS': 'Entrees',
  'BUILD YOUR OWN BOWL': 'Entrees',
  'BYO': 'Entrees',
  'PLATES': 'Entrees',
  'CLASSIC INDIAN PLATES': 'Entrees',
  'BURRITOS': 'Entrees',
  'INDIAN BURRITOS': 'Entrees',
  'CHEF CURATED BOWLS': 'Entrees',
  'SIDES': 'Sides',
  'DRINKS': 'NA Drinks',
  'Cold Drinks': 'NA Drinks',
  'Hot Drinks': 'NA Drinks',
  'SWEETS': 'Sweets',
  'KIDS': 'Kids Meal',
  'Beer': 'Alc Drinks',
  'Wine': 'Alc Drinks',
  'Liquor': 'Alc Drinks',
  'Gameday': 'Alc Drinks',
};

// From AppScript: GRP_TO_SUBCATEGORY
export const GRP_TO_SUBCATEGORY: Record<string, string> = {
  'BOWLS': 'Bowl',
  'BUILD YOUR OWN BOWL': 'Bowl',
  'BYO': 'Bowl',
  'PLATES': 'Plates',
  'CLASSIC INDIAN PLATES': 'Plates',
  'BURRITOS': 'Burrito',
  'INDIAN BURRITOS': 'Burrito',
  'CHEF CURATED BOWLS': 'Bowl',
  'KIDS': 'Kids Meal',
  'Beer': 'Beer',
  'Wine': 'Wine',
  'Liquor': 'Liquor',
  'Gameday': 'Gameday',
};

// From AppScript: ITEM_SUBCATEGORY — item-level overrides (Sides + Drinks)
export const ITEM_SUBCATEGORY: Record<string, string> = {
  // Sides
  'Garlic Naan': 'Bread', 'Naan': 'Bread', 'Roti': 'Bread',
  'Mini Samosas': 'Samosa', 'Samosa Chaat': 'Samosa',
  'Cucumber Raita': 'Raita',
  'Side of Main': 'Main', 'Side of Grain': 'Grain',
  'Side of Veggie': 'Veggie', 'Side of Sauce': 'Sauce',
  'Chips + Chutney': 'Chips',
  'That Fire Hot Sauce - Side': 'Sauce Bottle',
  'That Fire Hot Sauce (Bottle)': 'Sauce Bottle',
  // Drinks
  'Mango Lassi': 'Lassi', 'Strawberry Lassi': 'Lassi', 'Blossom Lassi': 'Lassi',
  'Mango Lassi for a Group - 1/2 Gallon': 'Lassi',
  'Homemade Juice': 'Juice',
  'Handcrafted Juice for a Group - 1/2 Gallon': 'Juice',
  'Maine Root Fountain Soda': 'Canned Soda',
  'Olipop - Cola': 'Canned Soda', 'Olipop - Lemon Lime': 'Canned Soda',
  'Olipop - Root Beer': 'Canned Soda',
  'Spindrift - Lemon': 'Canned Soda', 'Spindrift - Grapefruit': 'Canned Soda',
  'LaCroix - Lime': 'Canned Soda', 'LaCroix - Grapefruit': 'Canned Soda',
  'Open Water Still Water': 'Water', 'Open Water Sparkling Water': 'Water',
  'Wild Kombucha - Mango Peach': 'Kombucha', 'Wild Kombucha - Ginger': 'Kombucha',
  'Masala Chai': 'Chai', 'Masala Chai - Oat Milk': 'Chai',
  'Iced Oat Masala Chai': 'Chai', 'Icaro - Spearmint Yerba Mate': 'Chai',
  'Fresh Young Coconut': 'Coconut',
  // Sweets
  'Masala Chai Cookies': 'Cookies', 'Sweet Cardamom Yogurt': 'Yogurt',
  'Mango Lassi Soft Serve': 'Soft Serve', 'Masala Chai Soft Serve': 'Soft Serve',
  'Swirl Soft Serve': 'Soft Serve', 'Chocolate Chai Soft Serve': 'Chai',
  // Alc overrides
  'Spiked Lassi': 'Liquor', 'Tamarind Margarita': 'Liquor',
  'Pabst Blue Ribbon - Gameday': 'Gameday',
};

// Item-level category overrides
export const ITEM_CATEGORY_OVERRIDE: Record<string, string> = {
  'That Fire Hot Sauce (Bottle)': 'Retail',
  'That Fire Hot Sauce - Side': 'Retail',
};

export const ITEM_SUBCATEGORY_OVERRIDE: Record<string, string> = {
  'BYO Indian Burrito': 'Burrito',
};

export function getCategory(canonicalName: string, menuGroup: string): string {
  if (ITEM_CATEGORY_OVERRIDE[canonicalName]) return ITEM_CATEGORY_OVERRIDE[canonicalName];
  return GRP_TO_CATEGORY[menuGroup] || '';
}

export function getSubCategory(canonicalName: string, menuGroup: string): string {
  if (ITEM_SUBCATEGORY_OVERRIDE[canonicalName]) return ITEM_SUBCATEGORY_OVERRIDE[canonicalName];
  if (ITEM_SUBCATEGORY[canonicalName]) return ITEM_SUBCATEGORY[canonicalName];
  return GRP_TO_SUBCATEGORY[menuGroup] || '';
}
```

### 5b. OFFSITE channel — special category rule

**When `menu_name = 'OFFSITE POP-UPS'`:**
- `category` = `menu_group` value (the vendor name: Aramark / Fooda / Eurest / Metz / etc.)
- `sub_category` = NULL (no sub-category for offsite items)

This is different from the Catering hierarchy. Do NOT apply `GRP_TO_CATEGORY` for Offsite items.

```sql
-- Category resolution for OFFSITE
CASE
  WHEN fol.menu_name = 'OFFSITE POP-UPS' THEN fol.menu_group  -- vendor IS the category
  ELSE /* use GRP_TO_CATEGORY logic */
END AS category,
CASE
  WHEN fol.menu_name = 'OFFSITE POP-UPS' THEN NULL             -- no sub-category
  ELSE /* use GRP_TO_SUBCATEGORY logic */
END AS sub_category
```

### 5c. CATERING channel — category from item content

For `channel_code = 'CATERING'` (menu_name IN `('CATERING', 'CATERING - 3PD')`):
- Apply the same `GRP_TO_CATEGORY` / `GRP_TO_SUBCATEGORY` logic as standard channels
- The **vendor** is a separate dimension pulled from `alt_payment_name` in `br_order_payment`
- Display: Vendor (EzCater / Hungry / etc.) → Category (Entrees / Sides) → Item name

Get vendor per order:
```sql
SELECT DISTINCT ON (order_guid)
  order_guid, alt_payment_name
FROM public.br_order_payment
WHERE alt_payment_name IS NOT NULL
ORDER BY order_guid, amount DESC
```

### 5d. category_2 → Level 2 display category map (DB-based fallback)

When `GRP_TO_CATEGORY` has no match (item not in a standard menu group), fall back to `analytics.item_lookup`:

```typescript
// category_2 from analytics.item_lookup → display category
export const SUB_TO_CATEGORY: Record<string, string> = {
  // Entrees
  'Bowls': 'Entrees', 'Bowl': 'Entrees', 'Burritos': 'Entrees',
  'Burrito': 'Entrees', 'Plates': 'Entrees',
  // NA Drinks
  'Lassi': 'NA Drinks', 'Juice': 'NA Drinks', 'Chai': 'NA Drinks',
  'Canned Soda': 'NA Drinks', 'Water': 'NA Drinks',
  'Coconut': 'NA Drinks', 'Kombucha': 'NA Drinks',
  // Sides
  'Bread': 'Sides', 'Samosa': 'Sides', 'Raita': 'Sides',
  'Grain': 'Sides', 'Main': 'Sides', 'Sauce': 'Sides', 'Veggie': 'Sides', 'Chips': 'Sides',
  // Sweets
  'Cookies': 'Sweets', 'Soft Serve': 'Sweets', 'Yogurt': 'Sweets',
  // Kids Meal
  'Kids Meal': 'Kids Meal', 'KIDS': 'Kids Meal',
  // Alc Drinks
  'Beer': 'Alc Drinks', 'Wine': 'Alc Drinks', 'Liquor': 'Alc Drinks', 'Gameday': 'Alc Drinks',
  // Retail
  'Sauce Bottle': 'Retail', 'Retail': 'Retail',
};
```

### 5e. Category order (from AppScript `catOrder`)

Always display categories in this order:
```typescript
export const CATEGORY_ORDER = [
  'Entrees', 'Sides', 'NA Drinks', 'Sweets', 'Kids Meal', 'Retail', 'Alc Drinks'
];
```

---

## 6. ITEM NAME NORMALIZATION

The pipeline already applies this, so `canonical_name` in `fact_order_lines` is clean. Only needed when matching against external sources (Bikky, R365).

```typescript
// From AppScript: BYO_NAME_MAP
export const BYO_NAME_MAP: Record<string, string> = {
  'Grain Bowl': 'BYO Grain Bowl',
  'Salad Bowl': 'BYO Salad Bowl',
  'Greens + Grains Bowl': 'BYO Greens + Grains Bowl',
  'Grain Bowl - In House': 'BYO Grain Bowl',
  'Salad Bowl - In House': 'BYO Salad Bowl',
  'Greens + Grains Bowl - In House': 'BYO Greens + Grains Bowl',
  'byo Grain Bowl - In House': 'BYO Grain Bowl',
  'byo Salad Bowl - In House': 'BYO Salad Bowl',
  'byo Greens + Grains Bowl - In House': 'BYO Greens + Grains Bowl',
  'Cauliflower + Quinoa - In House': 'Spiced Cauli + Quinoa Bowl',
  'Cauliflower + Quinoa Bowl': 'Spiced Cauli + Quinoa Bowl',
  'Burrito - In House': 'BYO Indian Burrito',
  'Maine Root Fountain Soda - In House': 'Maine Root Fountain Soda',
  'Homemade Juice - In House': 'Homemade Juice',
};

export function normalizeItemName(rawName: string): string {
  return BYO_NAME_MAP[rawName] ?? rawName;
}
```

---

## 7. EXCLUSIONS

**Context:** The AppScript excludes these from its main PMIX sheets. On the dashboard they are excluded from IN_HOUSE / APP / TPD calculations and ME. They are **shown only in the dedicated Catering and Offsite tabs**.

```typescript
// From AppScript: EXCLUDED_MENUS (menus whose items go to dedicated Catering/Offsite tabs)
export const EXCLUDED_MENUS = new Set([
  'CATERING', 'CATERING - 3PD', 'OFFSITE POP-UPS'
]);

// From AppScript: EXCLUDED_GROUPS (menu groups that are operational/vendor-specific)
export const EXCLUDED_GROUPS = new Set([
  'Aramark', 'BAG TAX', 'Cater Cow', 'Catering Bundles',
  'Catering Packages - BYO Bowl Bar',
  'EzCater + Relish Individually Packaged Bowls',
  'EzCater Additional Items', 'EzCater Catering Packages',
  'EzCater Drinks', 'EzCater Sides + Sweets',
  'Fooda', 'HUNGRY', 'Metz', 'Sharebite', 'Taher',
  'TERRITORY', 'ZeroCater', 'Club Feast', 'Cureate',
  'EF Tours', 'Eurest',
  'Individually Packaged Bowls',
  'Individually Packaged Indian Burritos',
  'Individually Packaged Plates', 'Indian Burrito Boxes',
  '3PD MARKUPS', 'Additional Items',
]);

// Items always excluded regardless of "Real menu items only" toggle
export const EXCLUDED_ITEMS = new Set([
  'Delivery Fee', 'Utensils', '3PD MARKUP', 'BAG TAX'
]);
```

**"Real menu items only" toggle behavior:**
- When ON: applies all three exclusion sets above
- When OFF: shows everything (used for auditing; does NOT add Catering/Offsite to main metrics)

**Important:** Catering and Offsite items always stay in their own tabs. The toggle only affects whether operational line items are visible in the main tabs.

---

## 8. CORE METRIC CALCULATIONS

From AppScript `_masterCols_()` and `stepVerifyResults()`:

```
avg_price         = SUM(line_total) / SUM(quantity)
net_sales         = SUM(line_total)
quantity          = SUM(quantity)
total_cost        = avg_cost_with_mods × quantity
cogs_pct          = total_cost / net_sales
margin_pct        = 1 - cogs_pct          ← NOTE: derived from totals, NOT item-average
margin            = avg_price - avg_cost_with_mods
total_margin      = net_sales - total_cost
menu_mix_pct      = item_quantity / SUM(all_qty_in_scope)
sls_pct           = item_net_sales / SUM(all_net_sales)
category_mix_pct  = category_net_sales / SUM(all_net_sales)

-- Blended: WEIGHTED averages across all channels (not simple average)
blended_avg_price = SUM(net_sales_all_channels) / SUM(qty_all_channels)
blended_avg_cost  = SUM(total_cost_all_channels) / SUM(qty_all_channels)
blended_margin    = 1 - (blended_avg_cost / blended_avg_price)
```

**Margin threshold for "low margin" highlighting (from AppScript):**
```
avg_margin = SUM(total_margin_$) / SUM(net_sales_$)
-- NOT: average of per-item margin percentages
-- This is intentional — per-item average diverges by 3-4pp and causes wrong red highlighting
```

---

## 9. AVG COST WITH MODIFIERS (PINK SHEET LOGIC)

Exact replication of `getPinkCost_()` from the AppScript.

### 9a. Modifier types (from `analytics.modifier_type`)
```
Base | Main | Extra Main | Veggie | Extra Veggie
Sauce | Chutney and Dressings | Topping | Make it a Meal
```
`Make it a Meal` = combined Sides + Drinks + Sweets add-ons.

### 9b. Which modifiers are recorded per channel

| Channel | Modifier types recorded in Toast |
|---|---|
| APP + TPD | All types (full BYO data) |
| IN_HOUSE | Mains + Extra Mains + Make it a Meal only |
| CATERING / OFFSITE | Same as IN_HOUSE |

### 9c. Per-type cost
```
type_total_cost = SUM(modifier_qty × r365_modifier_cost.avg_cost)
-- modifier_qty = SUM(fact_modifiers.quantity) grouped by canonical_name
-- matched: fact_modifiers.parent_selection = fact_order_lines.selection_guid
-- r365 matched by modifier_name + modifier_type + period
```

### 9d. ½ and ½ weighted average
Modifiers named `"1/2 and 1/2 [type]"` (e.g. "1/2 and 1/2 Grains") have no direct cost. Compute as:
```
half_mods = all "1/2 X" variants under same parent + same modifier type
cost_of_½_and_½ = SUM(qty_of_"1/2 X" × cost_of_full_X) / SUM(qty_of_all_"1/2 X")
-- Use cost of "Basmati Rice", not "1/2 Basmati Rice"
```

### 9e. Modifier cost derivation rules (from AppScript `stepValidateImports`)
```typescript
// "Extra Chicken Tikka" → use cost of "Chicken Tikka"
if (mod.startsWith('Extra '))           baseCost = costMap[mod.slice(6)];
// "1/2 Basmati Rice" → cost of "Basmati Rice" / 2
if (mod.startsWith('1/2 '))             baseCost = costMap[mod.slice(4).replace(/^and /,'')] / 2;
// "Mint Cilantro Chutney - Side" → cost of "Mint Cilantro Chutney"
if (mod.endsWith(' - Side'))            baseCost = costMap[mod.slice(0,-7)];
// "Organic X" → cost of "X"
if (mod.startsWith('Organic '))         baseCost = costMap[mod.slice(8)];
// "X - Pick 2 Combo" → cost of "X"
if (mod.endsWith(' - Pick 2 Combo'))    baseCost = costMap[mod.slice(0,-15).trim()];
// Skip/No/Bag items → $0
if (/^skip|^no |utensil|plastic bag|bag|disposable/i.test(mod)) baseCost = 0;
```

### 9f. Plates rule
Items where `sub_category = 'Plates'` — **exclude Mains and Extra Mains** from modifier cost. All other types still apply.

```typescript
export const PLATE_ITEMS = new Set([
  'Butter Chicken', 'Saag Paneer', 'Saag Chole', 'Coconut Veggie Curry',
  'Chicken Tikka Masala', 'Aloo Gobhi', 'Paneer Butter Masala',
  'Chicken Curry', 'Lamb Kofta Korma', 'Paneer Tikka Masala',
]);
```

### 9g. Final formula
```
total_modifier_cost  = SUM(per-type totals in $)
total_base_cost      = r365_item_cost.avg_cost × quantity
modifier_plus_base   = total_modifier_cost + total_base_cost
avg_cost_with_mods   = modifier_plus_base / quantity

-- TPD: apply 1.18× to modifier cost only (not base item cost)
avg_cost_with_mods_tpd = r365_item_cost.avg_cost + (total_modifier_cost / quantity) × 1.18

-- Non-BYO items (Sides, Drinks, Sweets, Retail): no modifier uplift
avg_cost_with_mods = r365_item_cost.avg_cost
```

### 9h. 3PD rates (from AppScript constants)
```typescript
export const RATE_3PD_PRICE = 1.22;   // 3PD sells at 22% markup
export const RATE_3PD_COST  = 1.18;   // 3PD modifier cost uplift
```
Toast records 3PD prices at the 3PD price point — no manual price adjustment needed. Apply 1.18× to modifier cost only.

### 9i. R365 cost lookup fallback chain
```
1. r365_item_cost WHERE item_name = canonical_name AND period = current_period AND location = location_code
2. r365_item_cost WHERE item_name = canonical_name AND period = current_period  (company-wide)
3. r365_item_cost WHERE item_name = canonical_name  (most recent period)
4. NULL → flag "NO COST" badge; exclude from ME classification
```

Exceptions with no cost (expected): `Side of Main`, `Side of Veggie`, `Side of Grain` — these have no standalone R365 cost. Do not flag them.

---

## 10. MENU ENGINEERING (ME)

**Scope: IN_HOUSE + APP + TPD only.**
CATERING and OFFSITE are excluded from ME. They have their own tabs but no ME classification.

Recalculates dynamically on every filter change.

```typescript
// From AppScript: checkMaster() threshold logic
// engOrder from AppScript:
export const ME_ORDER = ['Dog', 'Plow Horse', 'Puzzle', 'Star'];

function classifyME(items: ItemRow[]) {
  const inScope = items.filter(i =>
    !i.isExcluded && i.quantity > 0 && i.avg_cost > 0 && i.has_category &&
    ['IN_HOUSE','APP','TPD'].includes(i.channel_code)
  );
  const N = inScope.length;
  if (N === 0) return items.map(i => ({ ...i, me: null }));

  // Threshold = SUM(total_margin$) / SUM(net_sales$) — NOT average of per-item margins
  const sumTotMgn = inScope.reduce((s, i) => s + i.total_margin, 0);
  const sumNS     = inScope.reduce((s, i) => s + i.net_sales, 0);
  const avgMarginThreshold = sumNS > 0 ? sumTotMgn / sumNS : 0;
  const menuMixThreshold   = (1 / N) * 0.7;

  return items.map(i => {
    if (!inScope.includes(i)) return { ...i, me: null };
    const marginClass = i.margin_pct >= avgMarginThreshold ? 'High' : 'Low';
    const mixClass    = i.menu_mix_pct >= menuMixThreshold  ? 'High' : 'Low';
    const me = {
      'High-High': 'Star',
      'High-Low':  'Puzzle',
      'Low-High':  'Plow Horse',
      'Low-Low':   'Dog',
    }[`${marginClass}-${mixClass}`];
    return { ...i, me };
  });
}
```

Info banner in ME tab:
`"Avg margin threshold: 75.4% · Menu mix threshold: 0.76% ((1/N) × 0.7) · Scope: In-House + App + 3PD"`

Exclude from ME:
- Items with `avg_cost = 0` or `avg_cost = null` → flag "Missing Avg Cost"
- Open items (no category)
- CATERING and OFFSITE items

---

## 11. PERIOD DEFINITIONS

```typescript
// From AppScript: RASA 4-week fiscal periods
export const PERIODS: Record<string, { start: string; end: string }> = {
  'P1 2026': { start: '2025-12-31', end: '2026-01-27' },
  'P2 2026': { start: '2026-01-28', end: '2026-02-24' },
  'P3 2026': { start: '2026-02-25', end: '2026-03-24' },
  'P4 2026': { start: '2026-03-25', end: '2026-04-22' },
  'P5 2026': { start: '2026-04-23', end: '2026-05-20' },
  'P6 2026': { start: '2026-05-21', end: '2026-06-17' },
  'P7 2026': { start: '2026-06-18', end: '2026-07-15' },
  'P8 2026': { start: '2026-07-16', end: '2026-08-12' },
};

// For cost table lookup: find which period a date falls in
export function getPeriodForDate(date: string): string | null {
  const d = new Date(date);
  for (const [name, { start, end }] of Object.entries(PERIODS)) {
    if (d >= new Date(start) && d <= new Date(end)) return name;
  }
  return null;
}
```

---

## 12. DYNAMIC CHART GRANULARITY

Every time-series chart on every tab must support these granularity options. The granularity selector lives in the top filter bar and applies globally.

```typescript
type Granularity = 'daily' | 'weekly' | 'period';

// SQL bucketing per granularity:
// daily:   GROUP BY business_date
// weekly:  GROUP BY DATE_TRUNC('week', business_date)
// period:  GROUP BY (whichever RASA period the date falls in)
```

The date filter determines the range; granularity determines how bars/points are grouped within that range.

**Period granularity SQL helper:**
```sql
-- Map each date to its period label
SELECT business_date,
  CASE
    WHEN business_date BETWEEN '2025-12-31' AND '2026-01-27' THEN 'P1 2026'
    WHEN business_date BETWEEN '2026-01-28' AND '2026-02-24' THEN 'P2 2026'
    WHEN business_date BETWEEN '2026-02-25' AND '2026-03-24' THEN 'P3 2026'
    WHEN business_date BETWEEN '2026-03-25' AND '2026-04-22' THEN 'P4 2026'
    WHEN business_date BETWEEN '2026-04-23' AND '2026-05-20' THEN 'P5 2026'
    WHEN business_date BETWEEN '2026-05-21' AND '2026-06-17' THEN 'P6 2026'
    WHEN business_date BETWEEN '2026-06-18' AND '2026-07-15' THEN 'P7 2026'
    WHEN business_date BETWEEN '2026-07-16' AND '2026-08-12' THEN 'P8 2026'
    ELSE 'Other'
  END AS period_label
FROM public.fact_order_lines
```

---

## 13. GLOBAL FILTER BAR

```
[Date dropdown ▾]  [Location: All ▾]  [Channel: All] [In-House] [App] [3PD] [Catering] [Offsite]
[Granularity: Daily ▾]  [Menu: All menus ▾]  [☑ Real menu items only]
```

**Date dropdown options:**
- Today / Yesterday
- This Week / Last Week
- Last 4 Weeks / Last 8 Weeks
- This Month / Last Month
- Q1 / Q2 / Q3 / Q4 2026
- P1 / P2 / P3 / P4 / P5 / P6 / P7 / P8 2026
- Custom range (from/to date pickers + Apply button)

**Granularity dropdown:** Daily | Weekly | By Period — affects how time-series bars are grouped

**Channel pills:** multi-select, updates all tabs and all API calls simultaneously

**"Real menu items only":** excludes `EXCLUDED_GROUPS` + `EXCLUDED_MENUS` + `EXCLUDED_ITEMS`

---

## 14. API ROUTES

All accept query params: `?start=&end=&location=&channel=&granularity=`

```
GET /api/summary          → KPIs: items_sold, net_revenue, avg_margin_pct, unique_items, top_item
GET /api/items            → Item mix with cost, ME, category, sub_category
GET /api/channels         → Channel breakdown + vendor drill-down
GET /api/locations        → Location cards + category mix matrix + item comparison
GET /api/byo              → Modifier breakdown per BYO item per type
GET /api/payments         → Payment sources breakdown
GET /api/me               → ME thresholds + quadrant counts + full item list (IH+APP+TPD only)
GET /api/catering         → Catering-specific data: vendor breakdown, item list, KPIs
GET /api/offsite          → Offsite-specific data: location breakdown, item list, KPIs
GET /api/open-items       → Items missing category or cost (open item flags)
GET /api/bikky            → Retention rates from Bikky tables
GET /api/renames          → Items with multiple historical raw names
GET /api/needs-review     → Orders with channel conflicts
GET /api/trend            → Time-series revenue + qty with granularity param
PATCH /api/channel-override → Set manual channel for an order_guid
```

---

## 15. DASHBOARD TABS

**Tab order:** Overview | Item Mix | Location Compare | Channel | BYO | Payments | Menu Engineering | Catering | Offsite | Open Items | Bikky | Renames | Needs Review | All Items

---

### Tab 1 — Overview

**5 KPI cards:** Items Sold | Net Revenue | Avg Margin % | Unique Items | Top Item

**Charts (all respect granularity selector):**
1. Sales trend — bar (revenue) + line (quantity) dual Y-axis, grouped by selected granularity
2. Revenue by channel — doughnut (5 channels)
3. Top 8 items by revenue — horizontal bar
4. Revenue by category — horizontal bar using `CATEGORY_ORDER`

All charts update dynamically when date, location, channel, or granularity filter changes.

---

### Tab 2 — Item Mix

- Search + GROUP BY (Category / Sub-Category / Menu Group) + metric toggle (Qty / Revenue / % Mix)
- Grouped collapsible table — group header row shows group totals
- Columns: Item | Menu Group | Category | Sub-Category | Qty | Revenue | Avg Price | % Mix | Orders
- Open items show with `OPEN ITEM` badge (no category)
- Items included in total even if open item (Rule C from Section 3)

---

### Tab 3 — Location Compare

**Top row — Location cards (one per location)**
- Location name chip (with location color)
- Revenue value large
- "X items sold"
- "Y% of group revenue"
- Optional event label

**Filter row:** `[All Locations ▾]  [Revenue ▾ (Revenue / Qty / % Mix)]`

**Middle row (two panels):**
- Left: Revenue by Location — vertical bar, colored by location
- Right: Category Mix by Location — matrix table
  - Rows = CATEGORY_ORDER values
  - Columns: CATEGORY | BALLPARK | MVT | MOSAIC | NL | ROCKVILLE
  - Values = category revenue as % of that location's total
  - Column headers in location colors

**Bottom: Item Comparison table**
- Controls: Search | Top 20 ▾ | Sort by Avg ▾ | High→Low ▾
- Columns: ITEM | BALLPARK | MVT | MOSAIC | NL | ROCKVILLE | AVG ↓
- Highest value per row → bold, `var(--accent)` text
- Location with no sales for item → `—`

---

### Tab 4 — Channel

**Channel KPI row:** one card per channel (In-House / App / 3PD / Catering / Offsite)
- Revenue | % of total | Δ vs prior same-length period

**Per-channel category breakdown bar charts (one per active channel):**
- IN_HOUSE / APP / TPD: bar by category (using CATEGORY_ORDER)
- Note: this tab shows overview. Detailed Catering and Offsite views are in their own tabs (8 & 9)

**Time-series panel:** Revenue by channel stacked/grouped bar, respects granularity

**Item-level channel split table:**
Columns: Item | ME badge | Total $ | IH $ | 3PD $ | App $ | Catering $ | Offsite $ | stacked mini-bar

---

### Tab 5 — BYO Breakdown

- Item selector: All BYO | BYO Grain Bowl | BYO Greens+Grains | BYO Salad Bowl | BYO Indian Burrito | Chicken Tikka Bowl
- Channel filter: All / In-House / App / 3PD
- Info banner: "Mains recorded on all orders. Bases, Sauce, Veggie, Topping, Chutney = online orders only."
- Sections per modifier type: Most popular Main | Extra Mains | Base | Sauce | Veggie | Toppings | Chutney+Dressing
- Each section: horizontal bar chart with item name + count + % of parent item orders

---

### Tab 6 — Payments

- KPI cards: Card Revenue | Alt Payment Revenue | Gift Card Revenue | Cash Revenue
- Stacked bar by location (Card / Alt Payment / Cash / Gift Card)
- Top payment sources horizontal bar
- Full table: Source | Type | Payment Count | Revenue | Avg Ticket | % Mix | mini-bar

---

### Tab 7 — Menu Engineering

**Scope badge at top:** "Analyzing: In-House + App + 3PD only"

**4 quadrant summary cards:**
- Stars → green `#10b981`
- Plow Horses → purple `var(--accent)`
- Puzzles → blue `#3b82f6`
- Dogs → red `#ef4444`

Each card: quadrant name + item count + % of revenue + description of what to do

**Info banner:** `"Avg margin threshold: 75.4% · Menu mix threshold: 0.76% ((1/N) × 0.7)"`

**ME scatter chart:** X = menu_mix_pct, Y = margin_pct, bubble size = net_sales, color by quadrant

**Revenue by ME quadrant:** doughnut

**Full item table:**
- Filter by quadrant (pills) + search
- Columns: Item | ME badge | Category | Sub-Category | Net Sales | Qty | Margin % | COGS % | Avg Price | Avg Cost
- Sort by any column
- Items with no cost → "Missing Cost" badge, excluded from ME

---

### Tab 8 — Catering

Dedicated tab for `channel_code = 'CATERING'` items. These are EXCLUDED from ME.

**4 KPI cards:** Total Revenue | Order Count | Avg Order Value | % of Total Revenue

**Vendor breakdown table:**
| Vendor | Orders | Revenue | AOV | % of Catering |
- EzCater, Hungry, Sharebite, Territory Foods, Cater Cow, WCK, ZeroCater, Cater2Me, Food Fleet

**Revenue trend chart:** time-series by granularity, colored `var(--ch-catering)`

**Category mix for Catering:**
- Bar chart: Revenue by category (using GRP_TO_CATEGORY on menu_group)
- Note: Catering items DO have categories (Entrees / Sides / etc.) — use same category resolution as standard items

**Top Catering items table:**
- Detection: `channel_code = 'CATERING'`
- Category = `GRP_TO_CATEGORY[menu_group]` (same logic as IN_HOUSE)
- Columns: Item | Menu Group | Category | Qty | Revenue | Avg Price | % of Catering

**Day-of-week heatmap:** orders by day of week (Mon–Sun)

---

### Tab 9 — Offsite

Dedicated tab for `channel_code = 'OFFSITE'` items. These are EXCLUDED from ME.

**Detection:** `channel_code = 'OFFSITE'` which means `menu_name = 'OFFSITE POP-UPS'`

**Category resolution for Offsite — special rule:**
- `category` = `menu_group` (the vendor field: Aramark / Fooda / Eurest / Metz / Taher / etc.)
- `sub_category` = NULL — no sub-category for Offsite items
- Do NOT apply `GRP_TO_CATEGORY` for Offsite items

**4 KPI cards:** Total Revenue | Order Count | Avg Order Value | % of Total Revenue

**Vendor breakdown (= category breakdown since category = menu_group):**
| Vendor / Category | Orders | Revenue | AOV | % of Offsite |
- Aramark, Fooda, Eurest, Metz, Taher, Foodworks, Cureate, Guest Services

**Revenue trend chart:** time-series by granularity, colored `var(--ch-offsite)`

**Top Offsite items table:**
- Columns: Item | Vendor (= menu_group) | Qty | Revenue | Avg Price | % of Offsite
- No sub-category column

**Day-of-week heatmap**

---

### Tab 10 — Open Items

Items meeting **any** of these conditions (after base filter from Section 3):
- `menu_group IS NULL OR menu_group = ''` AND `sales_category IS NOT NULL`
- Not in `analytics.item_lookup`
- In `analytics.item_lookup` but `category_2` does not map to a known category

These ARE included in revenue/qty totals (Rule C). They are flagged here for resolution.

**4 KPI cards:** Open Item Count | Revenue Affected | Missing Cost Count | Uncategorized Count

**Filter bar:** Search | Issue Type (ALL / NO COST / UNCATEGORIZED / MISSING MENU GROUP / WRONG CHANNEL) | Date | Location

**Issue badge types:**
- `NO COST` amber — not in `r365_item_cost` for current period
- `UNCATEGORIZED` blue — not in `item_lookup` or category_2 unresolvable
- `MISSING MENU GROUP` red — `menu_group` is null/blank
- `WRONG CHANNEL` purple — `channel_code` conflicts with `alt_payment_name`

**Table:**
| Item | Issue | Sales Category | Menu Group | Channel | Qty | Revenue | Last Seen | Action |

**Expandable row detail:** all raw DB values + suggested fix hint

**Pagination:** 25 rows per page

---

### Tab 11 — Customer Retention (Bikky)

Data source: `public.fact_bikky_instore` (IH + App) and `public.fact_bikky_3pd_loyalty` (3PD)

**4 KPI cards:** Avg Return Rate | Avg Reorder Rate | Best Retention Item | Worst Retention Item

**Table:**
| Item | Category | Revenue | Qty | Guests | Return Rate | Reorder Rate | Δ vs prev period |

Return rate color codes (from AppScript):
- Green: ≥ 28%
- Amber: 15–28%
- Red: < 15%

---

### Tab 12 — Renames Audit

- Banner: count of items with multiple historical names
- Table: Canonical Name | Category | All historical names (old → strikethrough) | Lifetime Qty | Lifetime $ | Locations | First Seen

---

### Tab 13 — Needs Review

- Warning banner: count of orders needing a channel decision
- Per-order cards: issue tag | location/date/amount | current channel | dining_option | alt_payment | suggested channel
- Inline override: channel dropdown + Set button → `PATCH /api/channel-override`
- "Apply All Suggestions" button

---

### Tab 14 — All Items

Full sortable table. Includes open items (flagged).

Columns: Item | ME badge | Category | Sub-Category | Net Sales | Qty | Margin % | COGS % | Avg Price | Avg Cost | Total Margin | Mix %

- Sort by any column
- Filter: quadrant pills + search + category filter
- Export CSV button

---

## 16. OPEN ITEMS CATEGORY RESOLUTION SQL

Use this CTE in every API query that needs category:

```sql
WITH item_cats AS (
  SELECT DISTINCT ON (cleaned_item_name)
    cleaned_item_name,
    category_2 AS sub_category
  FROM analytics.item_lookup
  WHERE cleaned_item_name NOT IN ('3PD MARKUP','Delivery Fee','Utensils','BAG TAX')
  ORDER BY cleaned_item_name, loaded_at DESC
),
period_map AS (
  SELECT business_date,
    CASE
      WHEN business_date BETWEEN '2025-12-31' AND '2026-01-27' THEN 'P1 2026'
      WHEN business_date BETWEEN '2026-01-28' AND '2026-02-24' THEN 'P2 2026'
      WHEN business_date BETWEEN '2026-02-25' AND '2026-03-24' THEN 'P3 2026'
      WHEN business_date BETWEEN '2026-03-25' AND '2026-04-22' THEN 'P4 2026'
      WHEN business_date BETWEEN '2026-04-23' AND '2026-05-20' THEN 'P5 2026'
      WHEN business_date BETWEEN '2026-05-21' AND '2026-06-17' THEN 'P6 2026'
      WHEN business_date BETWEEN '2026-06-18' AND '2026-07-15' THEN 'P7 2026'
      WHEN business_date BETWEEN '2026-07-16' AND '2026-08-12' THEN 'P8 2026'
      ELSE 'Other'
    END AS period_label
  FROM public.fact_order_lines
)
SELECT
  fol.*,
  ic.sub_category,
  CASE
    -- OFFSITE POP-UPS: category = menu_group, no sub_category
    WHEN fol.menu_name = 'OFFSITE POP-UPS' THEN fol.menu_group

    -- Standard GRP_TO_CATEGORY lookup
    WHEN fol.menu_group IN ('BOWLS','BUILD YOUR OWN BOWL','BYO','PLATES',
         'CLASSIC INDIAN PLATES','BURRITOS','INDIAN BURRITOS','CHEF CURATED BOWLS') THEN 'Entrees'
    WHEN fol.menu_group IN ('SIDES') THEN 'Sides'
    WHEN fol.menu_group IN ('DRINKS','Cold Drinks','Hot Drinks') THEN 'NA Drinks'
    WHEN fol.menu_group = 'SWEETS' THEN 'Sweets'
    WHEN fol.menu_group = 'KIDS' THEN 'Kids Meal'
    WHEN fol.menu_group IN ('Beer','Wine','Liquor','Gameday') THEN 'Alc Drinks'

    -- Item-level category overrides
    WHEN fol.canonical_name IN ('That Fire Hot Sauce (Bottle)','That Fire Hot Sauce - Side') THEN 'Retail'

    -- Fallback to item_lookup sub_category map
    WHEN ic.sub_category IN ('Bowls','Bowl','Burritos','Burrito','Plates') THEN 'Entrees'
    WHEN ic.sub_category IN ('Lassi','Juice','Chai','Canned Soda','Water','Coconut','Kombucha') THEN 'NA Drinks'
    WHEN ic.sub_category IN ('Bread','Samosa','Raita','Grain','Main','Sauce','Veggie','Chips') THEN 'Sides'
    WHEN ic.sub_category IN ('Cookies','Soft Serve','Yogurt') THEN 'Sweets'
    WHEN ic.sub_category IN ('Kids Meal','KIDS') THEN 'Kids Meal'
    WHEN ic.sub_category IN ('Beer','Wine','Liquor','Gameday') THEN 'Alc Drinks'
    WHEN ic.sub_category IN ('Sauce Bottle','Retail') THEN 'Retail'

    ELSE NULL  -- NULL = open item (uncategorized)
  END AS category
FROM public.fact_order_lines fol
LEFT JOIN item_cats ic ON ic.cleaned_item_name = fol.canonical_name
WHERE NOT fol.is_voided
  AND NOT fol.is_deferred
  AND NOT (
    (fol.menu_group IS NULL OR fol.menu_group = '')
    AND (fol.sales_category IS NULL OR fol.sales_category = '')
  )
```

---

## 17. VERIFICATION RULES

From AppScript `stepVerifyResults()`:

```
avg_price    = net_sales / quantity              tolerance: $0.01
total_cost   = avg_cost × quantity               tolerance: $1.00
cogs_pct     = total_cost / net_sales            tolerance: 0.1%
margin_pct   = 1 - cogs_pct                      tolerance: 0.1%
margin       = avg_price - avg_cost              tolerance: $0.01
blended_qty  = IH_qty + LO_qty + TPD_qty        tolerance: 0.5
```

---

## 18. DATA FRESHNESS

- Header badge: `SELECT MAX(business_date) FROM public.fact_order_lines`
- Manual refresh button → invalidates all React Query caches
- React Query stale time: 5 minutes

---

## 19. ENV VARS

```
DATABASE_URL=postgresql://...@...neon.tech/neondb?sslmode=require
```

---

## 20. IMPLEMENTATION NOTES

### Channel → tab routing
| channel_code | Primary tab | In ME? | In main totals? |
|---|---|---|---|
| IN_HOUSE | Overview, Item Mix, ME | ✅ | ✅ |
| APP | Overview, Item Mix, ME | ✅ | ✅ |
| TPD | Overview, Item Mix, ME | ✅ | ✅ |
| CATERING | Catering tab only | ❌ | ❌ in main; ✅ in Catering tab |
| OFFSITE | Offsite tab only | ❌ | ❌ in main; ✅ in Offsite tab |

### Open items in totals
- `menu_group` blank + `sales_category` present → **include** in revenue/qty totals, show `OPEN ITEM` badge
- Both blank → **exclude** completely

### Catering vs Offsite detection
- CATERING: `channel_code = 'CATERING'` (set by pipeline from alt_payment_name matching EzCater, Hungry, Sharebite, Territory, Cater Cow, WCK, ZeroCater)
- OFFSITE: `channel_code = 'OFFSITE'` OR directly `menu_name = 'OFFSITE POP-UPS'` (set by pipeline from alt_payment_name matching Fooda, Aramark, Eurest, Metz, Taher, Foodworks, Cureate)

### Catering category resolution
Catering items from menu CATERING/CATERING-3PD use same `GRP_TO_CATEGORY` as regular items — the menu_group field holds group names like BOWLS, SIDES, etc. just like in-house.

### Offsite category resolution
Offsite items from menu OFFSITE POP-UPS: the `menu_group` field holds the **vendor/location name** (Aramark / Fooda / Eurest / etc.), NOT a standard group name. This IS the category. No sub-category.
