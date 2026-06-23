# RASA PMIX Dashboard — Category & Sub-Category Logic

This corrects and fully specifies the category system. Prior prompts used incorrect field names.
Apply this on top of the main prompt and follow-up.

---

## ACTUAL TABLE STRUCTURE IN DB

```sql
analytics.item_lookup (
  raw_item_name,      -- original Toast display name (e.g. "Aloo Gobhi - Catering")
  cleaned_item_name,  -- canonical name (e.g. "Aloo Gobhi")
  category_1,         -- item group name — same as cleaned_item_name in most rows
  category_2,         -- sub-type: Bowls / Plates / Burritos / Lassi / Bread / etc.
  loaded_at
)
```

**Important:** `category_1` in this table is NOT "Entrees" / "NA Drinks" / "Sides". It equals `cleaned_item_name` in most rows. The proper display categories must be derived from `category_2` using a mapping (see below).

---

## THREE-LEVEL CATEGORY HIERARCHY

The dashboard must present three levels for IN_HOUSE / APP / TPD items:

```
Level 1 — Channel:        IN_HOUSE | APP | TPD | CATERING | OFFSITE
Level 2 — Category:       Entrees | NA Drinks | Sides | Sweets | Kids Meal | Alc Drinks | Retail
Level 3 — Sub-category:   Bowl | Plates | Burrito | Lassi | Juice | Bread | Samosa | ...
```

For CATERING and OFFSITE the hierarchy is different (see below).

---

## CATEGORY_2 → CATEGORY (LEVEL 2) MAPPING

Derive **Level 2** from `analytics.item_lookup.category_2` using this map:

```
category_2 value(s)                         → Category (Level 2)
────────────────────────────────────────────────────────────────
Bowls, Bowl, Burritos, Burrito, Plates,     → Entrees
  Salad, Roti

Lassi, Juice, Chai, Canned Soda, Water,     → NA Drinks
  Coconut, Kombucha

Bread, Samosa, Raita, Grain, Main,          → Sides
  Sauce, Veggie

Cookies, Soft Serve, Yogurt, Chai Soft Serve→ Sweets

Kids Meal, KIDS                             → Kids Meal

Beer, Wine, Liquor, Gameday                 → Alc Drinks

Sauce Bottle, Retail                        → Retail
```

Items whose `category_2` does not match anything above → flag as **UNCATEGORIZED** (Open Items tab).

Store this map as a constant in the codebase, not in the DB, since it rarely changes.

---

## FULL category_2 VALUES FROM LIVE DB

These are all distinct `category_2` values currently in `analytics.item_lookup`:

**→ Entrees**
- `Bowls` — BYO bowls, chef bowls (Chicken Tikka Bowl, Grain Bowl, etc.)
- `Plates` — Butter Chicken, CTM, Saag Paneer, Aloo Gobhi, Paneer Tikka Masala, etc.
- `Burritos` — Butter Chicken Burrito, Tandoori Paneer Burrito, etc.

**→ NA Drinks**
- `Lassi` — Mango Lassi, Strawberry Lassi, Blossom Lassi, Mango Lassi for a Group
- `Juice` — Homemade Juice, Handcrafted Juice for a Group
- `Chai` — Masala Chai, Iced Oat Masala Chai, Icaro Yerba Mate
- `Canned Soda` — Maine Root Fountain Soda, Olipop, LaCroix, Spindrift
- `Water` — Open Water Still, Open Water Sparkling
- `Kombucha` — Wild Kombucha variants

**→ Sides**
- `Bread` — Garlic Naan, Naan, Roti
- `Samosa` — Mini Samosas, Samosa Chaat
- `Raita` — Cucumber Raita
- `Grain` — Side of Grain
- `Main` — Side of Main
- `Sauce` — Side of Sauce
- `Veggie` — Side of Veggie

**→ Sweets**
- `Cookies` — Masala Chai Cookies
- `Soft Serve` — Masala Chai Soft Serve, Mango Lassi Soft Serve, etc.
- `Yogurt` — Sweet Cardamom Yogurt

**→ Kids Meal**
- `KIDS` — Kids Meal, Kids BYO

**→ Alc Drinks**
- `Beer` — Kingfisher, Nutrl, White Claw, DC Brau, Sixpoint, Dogfish Head, Rupee Lager, Pabst
- `Wine` — Borsao Granache, Indaba Sauvignon Blanc, Mont Gravet Rose
- `Liquor` — Mumbai Mule, Masala G+T, Tamarind Margarita
- `Gameday` — any `- Gameday` suffixed alc item (special pricing variant)

**→ Retail**
- `Sauce Bottle` — That Fire Hot Sauce (Bottle), That Fire Hot Sauce - Side

---

## LOOKUP RESOLUTION — how to get category for an order line

```sql
SELECT
  fol.canonical_name,
  fol.channel_code,
  il.category_2                        AS sub_category,   -- Level 3
  -- Level 2 derived in application code using the map above
  fol.line_total,
  fol.quantity
FROM public.fact_order_lines fol
LEFT JOIN analytics.item_lookup il
  ON il.cleaned_item_name = fol.canonical_name
WHERE NOT fol.is_voided
  AND NOT fol.is_deferred
```

**Matching rule:**
1. `canonical_name` from `fact_order_lines` → match to `analytics.item_lookup.cleaned_item_name`
2. If multiple rows match (same `cleaned_item_name`, different `raw_item_name`): take any — `category_2` is the same across all rows for a given `cleaned_item_name`
3. If no match: item is **UNCATEGORIZED** → flag in Open Items tab, show in dashboard without a category group

---

## CATERING & OFFSITE — DIFFERENT HIERARCHY

For CATERING and OFFSITE channels, the level-2 category is NOT from `item_lookup`. It is the **vendor/platform name** from `alt_payment_name`:

```
Level 1 — Channel:       CATERING                    OFFSITE
Level 2 — Vendor:        EzCater                     Fooda
                         Hungry                      Aramark
                         Sharebite                   Eurest
                         Territory Foods             Metz Corp
                         Cater Cow                   Taher
                         WCK                         Foodworks
                         Food Fleet                  Cureate
                         ZeroCater                   Guest Services
                         Cater2Me
Level 3 — Item type:     Entrees / Sides / Drinks / etc.
                         (from item_lookup.category_2 → mapped to Level 2 above)
```

For CATERING/OFFSITE vendor-specific items (e.g. "Aramark Chicken Bowl", "Fooda BYO Chicken Bowl"):
- `cleaned_item_name` = the vendor-specific item name (e.g. "Aramark Chicken Bowl")
- These have their own `category_2` entries in `item_lookup` (most are just the item name itself — they are one-off items)
- For vendor-specific items, the Level 3 sub-category = `category_2` from `item_lookup`
- If `category_2` = same as `cleaned_item_name` (i.e. no real sub-category), show the vendor as both Level 2 and omit Level 3

---

## OPEN ITEMS — CATEGORY DETECTION

An item is an **Open Item** (missing category) when ANY of these are true:

```sql
-- Missing from lookup entirely:
il.cleaned_item_name IS NULL

-- In lookup but category_2 not mappable to a Level 2 category:
-- (i.e. category_2 = cleaned_item_name — item name used as its own sub-category)
il.category_2 = il.cleaned_item_name

-- Or menu_group blank in fact_order_lines:
(fol.menu_group IS NULL OR fol.menu_group = '') AND fol.sales_category IS NOT NULL
```

---

## ITEMS WITH MULTIPLE LOOKUP ENTRIES

Some `raw_item_name` variants map to the same `cleaned_item_name` with the same category:

```
"Aloo Gobhi"            → Aloo Gobhi  | Plates
"Aloo Gobhi - Catering" → Aloo Gobhi  | Plates
"Aloo Gobhi - In House" → Aloo Gobhi  | Plates
```

Always join on `cleaned_item_name` — never on `raw_item_name`.

---

## SPECIAL CASES

### Catering bundles (The Party Pack, The Classic, etc.)
These appear in `item_lookup` with their own entries. They are CATERING-channel items.
- `category_2` = bundle name (e.g. "The Party Pack") — no standard sub-category
- Display as Level 2: vendor (from alt_payment_name), Level 3: "Bundle"

### Gameday items (e.g. "Kingfisher - Gameday")
- Same item as the non-gameday version but with different pricing
- `category_2` = "Gameday"
- Map to → Alc Drinks (Level 2), Gameday (Level 3)
- In Item Mix and ME, group with their non-gameday equivalent under the same canonical base name

### Delivery Fee, Utensils
- These are operational line items, NOT menu items
- Exclude from all revenue/mix/ME calculations
- `category_2` = "Delivery Fee" / "Utensils" → map to a hidden "Operational" category
- These should be caught by the "Real menu items only" filter checkbox

### 3PD MARKUP
- An accounting entry, not a real item
- Exclude from all calculations
- Already flagged in `item_lookup.category_1 = '3PD MARKUP'`

---

## RECOMMENDED DB VIEW

Create this view in the dashboard backend (not in the pipeline DB) for clean category resolution:

```sql
CREATE VIEW item_with_category AS
SELECT
  il.cleaned_item_name,
  il.category_2                                    AS sub_category,
  CASE
    WHEN il.category_2 IN ('Bowls','Plates','Burritos','Bowl','Burrito') THEN 'Entrees'
    WHEN il.category_2 IN ('Lassi','Juice','Chai','Canned Soda','Water','Coconut','Kombucha') THEN 'NA Drinks'
    WHEN il.category_2 IN ('Bread','Samosa','Raita','Grain','Main','Sauce','Veggie') THEN 'Sides'
    WHEN il.category_2 IN ('Cookies','Soft Serve','Yogurt') THEN 'Sweets'
    WHEN il.category_2 IN ('Kids Meal','KIDS') THEN 'Kids Meal'
    WHEN il.category_2 IN ('Beer','Wine','Liquor','Gameday') THEN 'Alc Drinks'
    WHEN il.category_2 IN ('Sauce Bottle','Retail') THEN 'Retail'
    ELSE NULL   -- NULL = uncategorized → Open Items flag
  END                                              AS category
FROM analytics.item_lookup il
WHERE il.cleaned_item_name NOT IN ('3PD MARKUP','Delivery Fee','Utensils');
```

Join this view in every API query that needs category:

```sql
LEFT JOIN item_with_category iwc ON iwc.cleaned_item_name = fol.canonical_name
```

`iwc.category` = Level 2 display category (Entrees / NA Drinks / Sides / Sweets / Kids Meal / Alc Drinks / Retail)
`iwc.sub_category` = Level 3 display sub-category (Bowls / Plates / Lassi / Bread / etc.)
