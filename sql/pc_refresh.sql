-- Precomputed modifier-cost layer refresh (full rebuild + atomic swap).
-- Runs automatically at the end of every merge_to_public() (see toast_pipeline/db.py),
-- or manually: python -m toast_pipeline.cli precompute
--
-- Feeds the dashboard's Pink Sheets / ME detail / BYO tabs via two tables:
--   analytics.pc_modifier_unit_cost  — resolved §1 unit cost per (modifier, period)
--                                      (MODIFIER_COST_FIX_SPEC.md lookup, incl. aliases,
--                                       extra/organic/1-2/side-of rules, chutney hardcode)
--   analytics.pc_modifier_daily      — daily-grain modifier facts with reader flags
--                                      (channel is Needs-Review-override-aware;
--                                       byo/detail/cmc scopes as column flags)
-- Parity: validated row-identical vs the live dashboard queries on P5-2026
-- (1,582/1,582 detail rows exact; BYO per-location exact; summary sums to the cent).
-- Rebuild takes ~70s; readers answer YTD in ~2s vs 557s computing live.
--
-- MUST STAY IN SYNC with lib/modifierCost.ts (aliases + fallbacks) and the
-- byo_fix map in the dashboard. Statements run in one transaction (no BEGIN needed).

CREATE TABLE IF NOT EXISTS analytics.pc_modifier_unit_cost_new (
      norm_name TEXT NOT NULL,
      pnum      INT  NOT NULL,
      unit_cost NUMERIC NOT NULL,
      src_pnum  INT  NOT NULL,
      PRIMARY KEY (norm_name, pnum)
    );

TRUNCATE analytics.pc_modifier_unit_cost_new;

-- src_pnum: the period this row's cost actually came from — equals pnum for a
-- direct match or a same-period structural derivation (extra/organic/1-2/side
-- variants, the hardcoded chutney cost, skip/no zeros), or an OLDER period
-- when falling back to the closest prior period's direct cost. Lets readers
-- (Pink Sheets) flag "this is a stale/borrowed cost, not this period's own."
-- Mirrors the unit_cost CASE below branch-for-branch — keep them in sync.
INSERT INTO analytics.pc_modifier_unit_cost_new (norm_name, pnum, unit_cost, src_pnum)
    WITH _mi AS (
      SELECT LOWER(clean_name) AS clean_name, cost_per_portion,
             RIGHT(period,4)::INT * 100 + SUBSTRING(period,2,2)::INT AS pnum
      FROM analytics.r365_modifier_cost
      WHERE recipe_name LIKE 'MI %' AND cost_per_portion > 0
    ),
    _names AS (
      SELECT DISTINCT LOWER(REGEXP_REPLACE(canonical_name, '\s*-\s*\*$', '')) AS norm_name
      FROM public.fact_modifiers
    ),
    _pnums AS (
      SELECT DISTINCT fiscal_year * 100 + period AS pnum FROM public.dim_fiscal_period
    ),
    _pairs AS (SELECT n.norm_name, p.pnum FROM _names n CROSS JOIN _pnums p),
    _cands AS (
      SELECT pr.norm_name, pr.pnum AS target_pnum, m.cost_per_portion,
             m.pnum AS src_pnum, (m.clean_name = pr.norm_name) AS is_direct
      FROM _pairs pr
      JOIN _mi m ON m.clean_name IN (pr.norm_name, CASE pr.norm_name
  WHEN 'tomato garlic (butter masala)' THEN 'tomato garlic sauce'
  WHEN 'tikka masala'                  THEN 'tikka masala sauce'
  WHEN 'tamarind chili (spicy)'        THEN 'tamarind chili sauce'
  WHEN 'peanut sesame'                 THEN 'peanut sesame sauce'
  WHEN 'coconut ginger'                THEN 'coconut ginger sauce'
  WHEN 'hungry tomato garlic'          THEN 'hungry tomato garlic sauce'
  WHEN 'hungry chili lime vinaigrette' THEN 'chili lime vinaigrette'
  WHEN 'hungry lamb kebab'             THEN 'lamb kebab'
  WHEN 'zerocater - pickled onions'    THEN 'zerocater pickled onions'
  WHEN 'zerocater - tandoori paneer'   THEN 'zerocater tandoori paneer'
  WHEN 'avocado - classic'             THEN 'avocado'
  WHEN 'avocado - party pack'          THEN 'avocado'
  WHEN 'sweet tamarind - classic'      THEN 'sweet tamarind chutney'
  WHEN 'harvest vegetables - classic'      THEN 'roasted vegetables - classic'
  WHEN 'harvest vegetables - party pack'   THEN 'roasted vegetables - party pack'
  -- R365 added dedicated Kids-portion recipes for these four drinks starting
  -- P07-2026 (MI Kokum Punch - Kids, MI Unsweetened Spiced Tea - Kids,
  -- MI Turmeric Ginger Lemonade - Kids, MI Mint Cardamom Limeade - Kids) --
  -- previously there was no Kids-specific cost so Turmeric Ginger
  -- Lemonade/Mint Cardamon Limeade borrowed the adult drink's cost, and
  -- Kokum Punch/Unsweetened Spiced Tea fell through unresolved at $0.
  -- Kids Golden Ginger Lemonade/Kids Mint Limeade (the live-menu renamed
  -- raw names) were missing from this list entirely -- also $0 before now.
  -- All raw order-time name variants (pre- and post- the live-menu rename,
  -- same as Kokum Punch/Ruby Citrus Cooler above) route to the one Kids
  -- recipe. Kids-only -- does not touch the adult resolution for any of
  -- these four drinks.
  WHEN 'kids kokum punch'              THEN 'kokum punch - kids'
  WHEN 'kids ruby citrus cooler'       THEN 'kokum punch - kids'
  WHEN 'kids unsweetened spiced tea'   THEN 'unsweetened spiced tea - kids'
  WHEN 'kids turmeric ginger lemonade' THEN 'turmeric ginger lemonade - kids'
  WHEN 'kids golden ginger lemonade'   THEN 'turmeric ginger lemonade - kids'
  WHEN 'kids mint cardamon limeade'    THEN 'mint cardamom limeade - kids'
  WHEN 'kids mint limeade'             THEN 'mint cardamom limeade - kids'
  WHEN 'kids unsweetened black tea'    THEN 'unsweetened spiced tea - kids'
  WHEN 'tandoori paneer'               THEN 'organic tandoori paneer'
  WHEN 'romaine'                       THEN 'shredded romaine'
  ELSE pr.norm_name END)
      WHERE m.pnum <= pr.pnum
    ),
    _primary AS (
      SELECT DISTINCT ON (norm_name, target_pnum) norm_name, target_pnum, cost_per_portion, src_pnum
      FROM _cands ORDER BY norm_name, target_pnum, src_pnum DESC, is_direct DESC
    )
    SELECT pr.norm_name, pr.pnum,
      CASE
        WHEN pr.norm_name LIKE 'skip %' OR pr.norm_name LIKE 'no %' THEN 0
        WHEN p.cost_per_portion IS NOT NULL THEN p.cost_per_portion
        WHEN pr.norm_name LIKE 'extra organic %' THEN COALESCE(
          (SELECT p2.cost_per_portion FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 15) AND p2.target_pnum = pr.pnum), 0)
        WHEN pr.norm_name LIKE 'extra %' THEN COALESCE(
          (SELECT p2.cost_per_portion FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 7) AND p2.target_pnum = pr.pnum), 0)
        WHEN pr.norm_name LIKE 'organic %' THEN COALESCE(
          (SELECT p2.cost_per_portion FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 9) AND p2.target_pnum = pr.pnum), 0)
        WHEN pr.norm_name LIKE '1/2 %' THEN COALESCE(
          (SELECT p2.cost_per_portion / 2.0 FROM _primary p2
           WHERE p2.norm_name = REGEXP_REPLACE(SUBSTRING(pr.norm_name FROM 5), '^and ', '', 'i')
             AND p2.target_pnum = pr.pnum), 0)
        WHEN pr.norm_name LIKE '% - side' THEN COALESCE(
          (SELECT p2.cost_per_portion FROM _primary p2
           WHERE p2.norm_name = LEFT(pr.norm_name, LENGTH(pr.norm_name) - 7)
             AND p2.target_pnum = pr.pnum), 0)
        WHEN pr.norm_name LIKE 'side of %' THEN COALESCE(
          (SELECT p2.cost_per_portion FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 9)
             AND p2.target_pnum = pr.pnum), 0)
        WHEN pr.norm_name IN ('spicy mango chutney', 'spicy mango chutney - side') THEN 0.1777
        ELSE 0
      END::NUMERIC AS unit_cost,
      CASE
        WHEN pr.norm_name LIKE 'skip %' OR pr.norm_name LIKE 'no %' THEN pr.pnum
        WHEN p.cost_per_portion IS NOT NULL THEN p.src_pnum
        WHEN pr.norm_name LIKE 'extra organic %' THEN COALESCE(
          (SELECT p2.src_pnum FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 15) AND p2.target_pnum = pr.pnum), pr.pnum)
        WHEN pr.norm_name LIKE 'extra %' THEN COALESCE(
          (SELECT p2.src_pnum FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 7) AND p2.target_pnum = pr.pnum), pr.pnum)
        WHEN pr.norm_name LIKE 'organic %' THEN COALESCE(
          (SELECT p2.src_pnum FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 9) AND p2.target_pnum = pr.pnum), pr.pnum)
        WHEN pr.norm_name LIKE '1/2 %' THEN COALESCE(
          (SELECT p2.src_pnum FROM _primary p2
           WHERE p2.norm_name = REGEXP_REPLACE(SUBSTRING(pr.norm_name FROM 5), '^and ', '', 'i')
             AND p2.target_pnum = pr.pnum), pr.pnum)
        WHEN pr.norm_name LIKE '% - side' THEN COALESCE(
          (SELECT p2.src_pnum FROM _primary p2
           WHERE p2.norm_name = LEFT(pr.norm_name, LENGTH(pr.norm_name) - 7)
             AND p2.target_pnum = pr.pnum), pr.pnum)
        WHEN pr.norm_name LIKE 'side of %' THEN COALESCE(
          (SELECT p2.src_pnum FROM _primary p2
           WHERE p2.norm_name = SUBSTRING(pr.norm_name FROM 9)
             AND p2.target_pnum = pr.pnum), pr.pnum)
        ELSE pr.pnum
      END AS src_pnum
    FROM _pairs pr
    LEFT JOIN _primary p ON p.norm_name = pr.norm_name AND p.target_pnum = pr.pnum;

CREATE TABLE IF NOT EXISTS analytics.pc_modifier_daily_new (
      business_date  DATE NOT NULL,
      pnum           INT,
      location_code  TEXT NOT NULL,
      raw_parent     TEXT NOT NULL,
      channel        TEXT NOT NULL,
      mod_display    TEXT NOT NULL,
      mod_norm       TEXT NOT NULL,
      section_base   TEXT,
      from_item_type BOOLEAN NOT NULL,
      pit_item_type  TEXT,
      include_cmc    BOOLEAN NOT NULL,
      byo_type       TEXT,
      in_byo_scope   BOOLEAN NOT NULL,
      qty            NUMERIC NOT NULL
    );

TRUNCATE analytics.pc_modifier_daily_new;

INSERT INTO analytics.pc_modifier_daily_new
    WITH byo_fix(raw, clean) AS (VALUES
      ('Grain Bowl','BYO Grain Bowl'), ('Salad Bowl','BYO Salad Bowl'),
      ('Greens + Grains Bowl','BYO Greens + Grains Bowl'),
      ('Cauliflower + Quinoa','Spiced Cauli + Quinoa Bowl'),
      ('Cauliflower + Quinoa Bowl','Spiced Cauli + Quinoa Bowl'),
      ('Kids BYO','Kids Meal'), ('Burrito','BYO Indian Burrito'),
      ('Grain Bowl - In House','BYO Grain Bowl'), ('Salad Bowl - In House','BYO Salad Bowl'),
      ('Greens + Grains Bowl - In House','BYO Greens + Grains Bowl'),
      ('Harvest Chicken Bowl - In House','BYO Greens + Grains Bowl'),
      ('Cauliflower + Quinoa - In House','Spiced Cauli + Quinoa Bowl'),
      ('Burrito - In House','BYO Indian Burrito'), ('Kids BYO - In House','Kids Meal'),
      ('Homemade Juice - In House','Homemade Juice'),
      ('Chicken Tikka Bowl - In House','Chicken Tikka Bowl'),
      ('Spicy Chili Chicken Bowl - In House','Spicy Chili Chicken Bowl'),
      ('Paneer Tikka Bowl - In House','Paneer Tikka Bowl'),
      ('Lamb Kebab Bowl - In House','Lamb Kebab Bowl'),
      ('Chicken Tikka + Avocado Salad - In House','Chicken Tikka + Avocado Salad'),
      ('Butter Chicken - In House','Butter Chicken'),
      ('Chicken Tikka Masala - In House','Chicken Tikka Masala'),
      ('Aloo Gobhi - In House','Aloo Gobhi'), ('Saag Paneer - In House','Saag Paneer'),
      ('Paneer Butter Masala - In House','Paneer Butter Masala'),
      ('Saag Chole - In House','Saag Chole'),
      ('Pick 2 Combo Plate - In House','Pick 2 Combo Plate'),
      ('Tandoori Paneer Burrito - In House','Tandoori Paneer Burrito'),
      ('Butter Chicken Burrito - In House','Butter Chicken Burrito')
    ),
    -- Display-only aliases for real order-time modifier names that are the SAME
    -- item as another real order-time name (verified via live volume + the
    -- item's own R365 recipe -- both names resolve to one recipe/cost), but
    -- differ in raw text so they'd otherwise show as two separate rows in
    -- Entree Mix. Deliberately does NOT touch mod_norm/cost resolution below --
    -- costing already resolves both names correctly on its own; this only
    -- unifies the display grouping. (owner-reported 2026-08-06)
    --   Tandoori Paneer / Organic Tandoori Paneer: both used concurrently every
    --   month, not a rename -- same R365 recipe (MI Tandoori Paneer -> clean
    --   name "Organic Tandoori Paneer"), just captured under 2 order-time labels.
    --   Chickpea Noodles -> Crispy Chickpea Noodles: clean cutover 2026-04-08,
    --   same R365 recipe (MI Chickpea Noodles) renamed on the menu.
    --   Turmeric Ginger Lemonade -> Golden Ginger Lemonade: clean cutover
    --   2026-03-27, same R365 recipe (MI Turmeric Ginger Lemonade) renamed.
    --   Unsweetened Spiced Tea -> Unsweetened Black Tea: clean cutover
    --   2026-04-02, same R365 recipe (MI Unsweetened Spiced Tea) renamed.
    --   Mint Cardamon Limeade (typo) -> Mint Cardamom Limeade -> Mint Limeade:
    --   two clean cutovers (2026-02-03, 2026-03-26), same R365 recipe (MI Mint
    --   Cardamom Limeade) renamed twice; both older names merge into the
    --   current live name.
    --   Kokum Punch -> Ruby Citrus Cooler: clean cutover 2026-03-27, same
    --   recipe renamed on the menu.
    --   Kids-tier equivalents of the 4 rename pairs above (same cutovers, Kids
    --   Meal portion of the same recipe) -- unifies each Kids pair into one
    --   display row same as the adult pair, but kept separate FROM the adult
    --   row since Kids is a different portion size/cost (owner-reported
    --   2026-08-24).
    mod_display_fix(raw, clean) AS (VALUES
      ('Organic Tandoori Paneer',  'Tandoori Paneer'),
      ('Chickpea Noodles',         'Crispy Chickpea Noodles'),
      ('Turmeric Ginger Lemonade', 'Golden Ginger Lemonade'),
      ('Unsweetened Spiced Tea',   'Unsweetened Black Tea'),
      ('Mint Cardamon Limeade',    'Mint Limeade'),
      ('Mint Cardamom Limeade',    'Mint Limeade'),
      ('Kokum Punch',              'Ruby Citrus Cooler'),
      ('Kids Turmeric Ginger Lemonade', 'Kids Golden Ginger Lemonade'),
      ('Kids Unsweetened Spiced Tea',   'Kids Unsweetened Black Tea'),
      ('Kids Mint Cardamon Limeade',    'Kids Mint Limeade'),
      ('Kids Kokum Punch',              'Kids Ruby Citrus Cooler')
    )
    SELECT
      fol.business_date,
      fp.fiscal_year * 100 + fp.period                     AS pnum,
      fol.location_code                                    AS location_code,
      fol.canonical_name                                   AS raw_parent,
      (COALESCE(co.correct_channel, CASE
  WHEN fol.menu_name IN ('FOOD - IN HOUSE', 'DRINKS - IN HOUSE') THEN 'IN_HOUSE'
  WHEN fol.menu_name IN ('APP', 'FOOD - TOAST ONLINE ORDERING')  THEN 'APP'
  WHEN fol.menu_name = 'DELIVERY'                                THEN 'TPD'
  WHEN fol.menu_name = '3PD OPEN MARKUP'                         THEN 'TPD_MARKUP'
  WHEN fol.menu_name = 'CATERING'                                THEN 'CATERING'
  WHEN fol.menu_name = 'CATERING - 3PD'                          THEN 'CATERING_3PD'
  WHEN fol.menu_name = 'OFFSITE POP-UPS'                         THEN 'OFFSITE'
  WHEN fol.menu_name IS NULL                                      THEN 'OPEN_ITEMS'
  ELSE 'OFFSITE' END))                                             AS channel,
      COALESCE(mdf.clean, REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', ''))  AS mod_display,
      LOWER(REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', ''))                         AS mod_norm,
      mt.modifier_type                                     AS section_base,
      mt.from_item_type                                    AS from_item_type,
      pit.item_type                                        AS pit_item_type,
      (
        EXISTS (SELECT 1 FROM analytics.modifier_type
                WHERE modifier_name = fm.canonical_name
                  AND modifier_type NOT LIKE 'Catering%'
                  AND modifier_type NOT IN ('NA','ZeroCater','Plate - Main','Online'))
        OR NOT EXISTS (SELECT 1 FROM analytics.modifier_type WHERE modifier_name = fm.canonical_name)
        OR fm.canonical_name = 'That Fire Hot Sauce'
      )                                                    AS include_cmc,
      bt.byo_type                                          AS byo_type,
      -- Homemade Juice / Maine Root Fountain Soda: only count the REAL flavor
      -- pick (Toast's own option_group_name for that choice), not every other
      -- modifier-shaped thing on the same line -- guests also attach free-text
      -- special instructions ("No ice please", "Item special instructions:
      -- (Please label for ...)") as modifier-like rows, which would otherwise
      -- get counted as bogus "flavors" alongside the real ones (owner-reported
      -- 2026-08-06).
      COALESCE(
        UPPER(fol.menu_group) IN (
          'BOWLS','BUILD YOUR OWN BOWL','BYO','CHEF CURATED BOWLS',
          'PLATES','CLASSIC INDIAN PLATES','BURRITOS','INDIAN BURRITOS','KIDS','KIDS MEAL')
        OR fol.canonical_name IN (
          'Side of Main','Side of Grain','Side of Sauce','Side of Veggie',
          'Handcrafted Juice for a Group - 1/2 Gallon')
        OR (fol.canonical_name = 'Homemade Juice' AND fm.option_group_name = 'Flavor?')
        OR (fol.canonical_name = 'Maine Root Fountain Soda' AND fm.option_group_name = 'Maine Root Flavor?')
      , FALSE)                                             AS in_byo_scope,
      SUM(fm.quantity)                                     AS qty
    FROM public.fact_modifiers fm
    JOIN public.fact_order_lines fol ON fm.parent_selection = fol.selection_guid
    LEFT JOIN mod_display_fix mdf ON mdf.raw = REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', '')
    LEFT JOIN analytics.channel_overrides co ON co.selection_guid = fol.selection_guid
    LEFT JOIN public.dim_fiscal_period fp
           ON fol.business_date >= fp.start_date::DATE
          AND fol.business_date <= fp.end_date::DATE
    LEFT JOIN LATERAL (
      SELECT p.item_type FROM analytics.parent_item_type p
      WHERE p.parent_item = fol.canonical_name
         OR (p.parent_item IN (SELECT raw FROM byo_fix WHERE clean = fol.canonical_name)
             AND p.item_type ILIKE '%' || CASE WHEN (COALESCE(co.correct_channel, CASE
  WHEN fol.menu_name IN ('FOOD - IN HOUSE', 'DRINKS - IN HOUSE') THEN 'IN_HOUSE'
  WHEN fol.menu_name IN ('APP', 'FOOD - TOAST ONLINE ORDERING')  THEN 'APP'
  WHEN fol.menu_name = 'DELIVERY'                                THEN 'TPD'
  WHEN fol.menu_name = '3PD OPEN MARKUP'                         THEN 'TPD_MARKUP'
  WHEN fol.menu_name = 'CATERING'                                THEN 'CATERING'
  WHEN fol.menu_name = 'CATERING - 3PD'                          THEN 'CATERING_3PD'
  WHEN fol.menu_name = 'OFFSITE POP-UPS'                         THEN 'OFFSITE'
  WHEN fol.menu_name IS NULL                                      THEN 'OPEN_ITEMS'
  ELSE 'OFFSITE' END)) IN ('APP','TPD','TPD_MARKUP')
                   THEN 'Online' ELSE 'In House' END)
      ORDER BY (p.parent_item = fol.canonical_name) DESC
      LIMIT 1
    ) pit ON true
    CROSS JOIN LATERAL (
      SELECT COALESCE(
        known.t,
        CASE WHEN LOWER(REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', '')) = 'that fire hot sauce' THEN 'Chutney And Dressing' END,
        CASE WHEN NOT EXISTS (
          SELECT 1 FROM analytics.modifier_type WHERE modifier_name = REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', '')
        ) THEN pit.item_type END
      ) AS modifier_type,
      (known.t IS NULL
       AND LOWER(REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', '')) <> 'that fire hot sauce'
       AND NOT EXISTS (
         SELECT 1 FROM analytics.modifier_type WHERE modifier_name = REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', '')
       )) AS from_item_type
      FROM (
        SELECT (SELECT amt.modifier_type FROM analytics.modifier_type amt
                WHERE amt.modifier_name = REGEXP_REPLACE(fm.canonical_name, '\s*-\s*\*$', '')
                  AND amt.modifier_type NOT LIKE 'Catering%'
                  AND amt.modifier_type NOT IN ('NA','ZeroCater','Plate - Main','Online')
                ORDER BY (amt.item_type = pit.item_type) DESC NULLS LAST
                LIMIT 1) AS t
      ) known
    ) mt
    LEFT JOIN LATERAL (
      -- BYO Breakdown semantics: strict item_type match on the UNSTRIPPED name
      SELECT LOWER(m2.modifier_type) AS byo_type
      FROM analytics.modifier_type m2
      JOIN (SELECT DISTINCT ON (parent_item) parent_item, item_type
            FROM analytics.parent_item_type ORDER BY parent_item, item_type) pit2
        ON pit2.parent_item = fol.canonical_name
      WHERE m2.modifier_name = fm.canonical_name
        AND m2.item_type     = pit2.item_type
        AND LOWER(m2.modifier_type) IN
          ('main','1/2 main','base','1/2 base','veggie','topping','sauce','chutney + dressing')
      LIMIT 1
    ) bt ON true
    WHERE NOT fol.is_voided AND NOT fol.is_deferred AND NOT fm.is_voided
      AND (
        fol.menu_name IN (
          'FOOD - IN HOUSE','DRINKS - IN HOUSE','APP','FOOD - TOAST ONLINE ORDERING',
          'DELIVERY','3PD OPEN MARKUP','CATERING','CATERING - 3PD','OFFSITE POP-UPS')
        OR (fol.menu_name IS NULL AND fol.sales_category IN ('Food','Drink'))
      )
    GROUP BY 1,2,3,4,5,6,7,8,9,10,11,12,13;

CREATE INDEX IF NOT EXISTS pc_mod_daily_new_date ON analytics.pc_modifier_daily_new (business_date);

CREATE INDEX IF NOT EXISTS pc_mod_daily_new_norm ON analytics.pc_modifier_daily_new (mod_norm, pnum);

SELECT COUNT(*) n FROM analytics.pc_modifier_unit_cost_new;

SELECT COUNT(*) n FROM analytics.pc_modifier_daily_new;

DROP TABLE IF EXISTS analytics.pc_modifier_unit_cost;
    DROP TABLE IF EXISTS analytics.pc_modifier_daily;
    ALTER TABLE analytics.pc_modifier_unit_cost_new RENAME TO pc_modifier_unit_cost;
    ALTER TABLE analytics.pc_modifier_daily_new RENAME TO pc_modifier_daily;
    ALTER INDEX analytics.pc_mod_daily_new_date RENAME TO pc_mod_daily_date;
    ALTER INDEX analytics.pc_mod_daily_new_norm RENAME TO pc_mod_daily_norm;

