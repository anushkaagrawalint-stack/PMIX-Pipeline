# R365 Modifier Cost — Known clean_name Corrections

R365's ModifierCost export (`Data/R365Data/ModifierCost/P*ModifierCost.xlsx`) sometimes
writes a `clean_name` (column B) that doesn't match the actual guest-facing modifier
name Toast records in `public.fact_modifiers.canonical_name`. When that happens, the
precompute layer's cost lookup misses and silently falls back to an older period's
cost (or $0), even though the recipe's own cost data is present and correct — see
`analytics.pc_modifier_unit_cost.src_pnum` and the dashboard's Pink Sheet `⚠` badge
(`is_stale_cost`), which is what originally surfaces these.

Every entry below was verified against real order data (`analytics.pc_modifier_daily`)
and/or `fact_modifiers.canonical_name` before being corrected — not guessed from
naming similarity alone.

`toast_pipeline/cli.py`'s `cmd_load_r365_modifier_cost` now applies these
automatically to every load (see `_KNOWN_CLEAN_NAME_FIXES`), so a period doesn't need
its xlsx manually patched — but the underlying xlsx files were also corrected in
place for P6 (2026-07-29) so the DB isn't the only place holding the fix.

**Scope policy (as of 2026-07-31):** the "Catering batch" reconciliation below is
being auto-applied only where the clean_name is catering/catering-3pd/offsite-scoped
and doesn't touch what the current (non-catering) IH/Online dashboard already reads.
23 recipe_names from that batch were held back from `_KNOWN_CLEAN_NAME_FIXES` because
their clean_name is ALSO a real modifier used in today's live BYO bowls (e.g.
`MI Basmati Rice` -> `Basmati Rice`, used in BYO Grain Bowl) — changing those wasn't
requested and risks affecting the current dashboard. **From P7 onward, ask before
adding any new fix outside strict catering scope.**

| recipe_name | wrong clean_name (as exported) | correct clean_name | found in |
|---|---|---|---|
| `MI Romaine` | Romaine | Romaine Lettuce | P6 |
| `MI 1/2 Romaine` | 1/2 Romaine | 1/2 Romaine Lettuce | P6 |
| `MI Romaine - Classic` | Romaine - Classic | Romaine Lettuce - Classic | P6 (also wrong in P1-P5, out of scope) |
| `MI Romaine - Desi Deluxe` | Romaine - Desi Deluxe | Romaine Lettuce - Desi Deluxe | P6 (also wrong in P1-P5, out of scope) |
| `MI Romaine - Party Pack` | Romaine - Party Pack | Romaine Lettuce - Party Pack | P6 (also wrong in P1-P5, out of scope) |
| `MI Shredded Romaine` | Shredded Romaine | Romaine | P6 (0 real orders for "Shredded Romaine"; 2,281 for bare "Romaine") |
| `MI 1/2 Harvest Veggies` | 1/2 Harvest Veggies | 1/2 Roasted Vegetables | P6 |
| `MI Kokum Vinaigrette Dressing` | Kokum Vinaigrette Dressing | Kokum Vinaigrette | P6 (consistently "Kokum Vinaigrette" in P1-P5) |
| `MI Tikka Masala Sauce` | Tikka Masala Sauce | Tikka Masala | P6 (6,054 real orders for bare "Tikka Masala"; 0 for "... Sauce") |
| `MI 1/2 Chicken Tikka` | 1/2 Chicken Tikka | 1/2 Chicken | P6 (3,551 real orders for "1/2 Chicken"; 0 for "... Tikka") |
| `MI Sautéed Spinach` | Sautéed Spinach (accented) | Sauteed Spinach | P3-P6 (P1-P2 had it right; Toast's real name has no accent) |
| `MI Sautéed Spinach - Classic` | Sautéed Spinach - Classic | Sauteed Spinach - Classic | Chronic since P1 |
| `MI Sautéed Spinach - Desi Deluxe` | Sautéed Spinach - Desi Deluxe | Sauteed Spinach - Desi Deluxe | Chronic since P1 |
| `MI Sautéed Spinach - Party Pack` | Sautéed Spinach - Party Pack | Sauteed Spinach - Party Pack | Chronic since P1 |
| `MI Sautéed Spinach - Side` | Sautéed Spinach - Side | Sauteed Spinach - Side | Chronic since P1 |
| `MI HUNGRY Sautéed Spinach` | HUNGRY Sautéed Spinach | HUNGRY Sauteed Spinach | Chronic since P1 |
| `MI That Fire Hot Sauce - Bottle` | That Fire Hot Sauce - Bottle (dash) | That Fire Hot Sauce (Bottle) (parens) | P6 |

**Deliberately NOT "fixed":**
- `MI Zerocater Sautéed Spinach` — Toast's real canonical name for this one IS
  accented (`ZeroCater - Sautéed Spinach`), so the accent is correct here.
- `TEST, Chicken Spice Mix v1`/`v2` — internal test recipes, never guest-ordered,
  don't correspond to any real modifier either way.
- `BATCH *` / `NEST *` recipes — internal prep/batch components, not customer-facing
  modifiers; not matched against `fact_modifiers` at all.

**If you find a new one:** verify it the same way — check `fact_modifiers.canonical_name`
for the real guest-facing spelling, and/or check `analytics.pc_modifier_daily` for real
order volume under each candidate name — before renaming. Don't rename based on naming
similarity alone; a couple of these turned out to need the *opposite* direction fix
than naming similarity alone would have suggested (e.g. `1/2 Chicken Tikka` looked like
a reasonable clarification of `1/2 Chicken`, but the real guest modifier is the bare
`1/2 Chicken` — the "clarified" version has zero real orders).

## Modifier name → recipe name reconciliation (for cost pulling)

This is a **different** mapping from the clean_name corrections above. Those fix a
wrong value already sitting in a sheet's column B for one recipe row. This table is
for modifier names that don't have (or won't have) their own dedicated recipe row in
R365 at all — e.g. a "- Side"/bottled variant of a sauce or dressing that's the exact
same batch recipe as the topping version, just sold as a standalone side. When pulling
cost for one of these modifier names, use the recipe listed here instead of looking
for a same-named recipe that doesn't exist.

Provided by Rahul; several of the right-hand ("- Side") modifier names have zero real
orders in `fact_modifiers` as of this writing (2026-07-31) — likely because they're new/
not yet ordered under IH+Online, or specific to catering (this table was compiled ahead
of the catering-modifier-cost work).

**Wired in (2026-08-03):** this reconciliation is now live as `CATERING_MOD_ALIAS_CTE`
in PMIX-Dashboard's `lib/queries.ts`, consulted by `getCateringPinkSheetDetails`/
`getCateringPinkSheets` only — it redirects a real order-time modifier name to
another name's cost when it has no recipe row of its own. Scoped entirely inside
those two catering-only queries, so it can't affect `r365_modifier_cost.clean_name`,
`pc_modifier_unit_cost`, or IH/Online costing regardless of what the underlying
recipe's shared clean_name currently is. Verified against the full P6 catering/
catering-3PD dataset (306 modifier rows) — every entry with a known recipe now
resolves to real cost; the only remaining $0/blank rows are free-text guest notes
mis-recorded as modifiers, `Skip Main` (intentional zero, by design), and the
already-documented no-recipe-provided gaps below.

One entry from the original list was found to be unnecessary/wrong and removed:
`Tomato Garlic (Butter Masala)` already resolves to real cost on its own (via the
pipeline's existing prior-period fallback) — aliasing it to `Tomato Garlic Sauce`
would have overridden a working value with a worse one.

| Modifier Name | RecipeName |
|---|---|
| 1/2 Romaine Lettuce | `MI 1/2 Romaine` |
| Chili Lime Vinaigrette | `MI Chili Lime Vinaigrette Dressing` |
| Chili Lime Vinaigrette - Side | `MI Chili Lime Vinaigrette Dressing` |
| Crispy Chickpea Noodles | `MI Chickpea Noodles` |
| Extra Lamb Kebab | `MI Lamb Kebab Meatballs` |
| Extra Organic Tandoori Paneer | `MI Tandoori Paneer` |
| Extra Roasted Vegetables | `MI Harvest Veggies` |
| Ginger Tamarind Chutney | `MI Tamarind Ginger Chutney` |
| Ginger Tamarind Chutney - Side | `MI Tamarind Ginger Chutney` |
| Golden Ginger Lemonade | `MI Turmeric Ginger Lemonade` |
| Kokum Vinaigrette | `MI Kokum Vinaigrette Dressing` |
| Kokum Vinaigrette - Side | `MI Kokum Vinaigrette Dressing` |
| Lamb Kebab | `MI Lamb Kebab Meatballs` |
| Mint Limeade | `MI Mint Cardamom Limeade` |
| Organic Tandoori Paneer | `MI Tandoori Paneer` |
| Roasted Vegetables | `MI Harvest Veggies` |
| Roasted Vegetables - Kids | `MI Harvest Veggies - Kids` |
| Romaine Lettuce | `MI Romaine` |
| Ruby Citrus Cooler | `MI Kokum Punch` |
| Shredded Paneer Cheese | `MI Shredded Paneer` |
| Unsweetened Black Tea | `MI Unsweetened Spiced Tea` |

### Catering batch

Also provided by Rahul, specifically for the catering-modifier-cost branch. Covers
"- Classic"/"- Party Pack" portion-size variants, "Catering - Additional Item - X"
add-ons, ZeroCater-branded modifiers, and basket/tray-size items (chai cookies, naan,
samosas) — all reconciled to their underlying R365 recipe the same way as above.

5 names had no recipe provided at all (left blank in the source) — no known R365
recipe exists for these yet:
- `Spicy Mango Chutney - Classic`
- `Spicy Mango Chutney - Party Pack`
- `Sweet Tamarind - Party Pack`
- `ZeroCater - Pickled Onions`
- `ZeroCater - Tandoori Paneer`

One entry worth flagging as-is (not verified further, just carried over as given):
- `Coconut Ginger` → `Coconut Ginger` — the only row where RecipeName has no `MI ` prefix.

**Resolved (owner confirmed 2026-08-01):** `MI Garlic Naan Basket Small`'s clean_name
is `Garlic Naan - Small (serves 10)` — now in `_KNOWN_CLEAN_NAME_FIXES`. Verified zero
current IH/Online usage under either name, so this was safe under the P6 scope
restriction.

**Correction (2026-08-03):** the original table above (and the note that used to be
here) had `Plain Naan - Small (serves 10)` sharing `MI Garlic Naan Basket Small`'s
recipe — that was wrong. `MI Plain Naan Basket Small` is a genuinely separate recipe
with its own real cost every period (e.g. $5.7059 at P6), the same pattern as the
already-correct `MI Plain Naan Basket Large` → `Plain Naan - Large (serves 20)`. Now
`_KNOWN_CLEAN_NAME_FIXES["MI Plain Naan Basket Small"] = "Plain Naan - Small (serves 10)"`
directly — no alias needed. Verified zero current IH/Online usage.

**Resolved (owner confirmed 2026-08-03):** the `MI Harvest Veggies` family had
several rows torn between "Roasted Vegetables ..." and "Harvest Vegetables ..." —
owner confirmed "Roasted Vegetables" is the recipe's correct clean_name. Now in
`_KNOWN_CLEAN_NAME_FIXES`:
- `MI Harvest Veggies - Classic` → `Roasted Vegetables - Classic`
- `MI Harvest Veggies - Party Pack` → `Roasted Vegetables - Party Pack`
- `MI Harvest Vegetables - Catering - Additional Item` → `Catering - Additional Item - Roasted Vegetables`

**Correction (2026-08-03, found via P6 CSV validation):** "Harvest Vegetables ..."
is NOT noise — it's a real, separately-ordered modifier name in its own right,
distinct from "Roasted Vegetables ...". Real P6 order volume: `Harvest Vegetables -
Classic` 249 qty, `Harvest Vegetables - Party Pack` 84 qty, `Catering - Additional
Item - Harvest Vegetables` 6 qty — all currently $0/no cost match. Same situation as
the "- Side" gaps: same underlying recipe/cost as the "Roasted Vegetables ..."
counterpart, just no dedicated recipe row of its own. Added to the modifier-name→
recipe lookup list below (still needs that mechanism built to actually pull cost):
- `Harvest Vegetables - Classic` → `MI Harvest Veggies - Classic`
- `Harvest Vegetables - Party Pack` → `MI Harvest Veggies - Party Pack`
- `Catering - Additional Item - Harvest Vegetables` → `MI Harvest Vegetables - Catering - Additional Item`

**Deliberately NOT included:** `MI Harvest Veggies - Kids` → `Roasted Vegetables - Kids`
is a separate, already-decided case — that clean_name is used by the current live
Kids Meal item, out of scope under the catering-only-for-now restriction (same
reasoning as the 23 entries held back earlier).

**Resolved (owner confirmed 2026-08-03):** `MI Avocado`'s clean_name stays `Avocado`
(bare) — no change, protects the 3,294 real IH/Online orders using that name today.
`Avocado - Classic` (catering) shares this same recipe/cost but has no dedicated row
of its own — it's added to the modifier-name→recipe lookup list above; still needs
that mechanism built before it can actually pull cost.

**Still unresolved:**
- `Spicy Mango Chutney - Classic` and `Sweet Tamarind - Party Pack` — no recipe name
  was ever provided for these (blank in the source); nothing to map to yet.

| Modifier/Item Name | RecipeName |
|---|---|
| 10 Chai Cookie Basket | `MI Masala Chai Cookies - 10` |
| 20 Chai Cookie Basket | `MI Masala Chai Cookies - 20` |
| Basmati Rice - Classic | `MI Basmati Rice - Classic` |
| Basmati Rice - Party Pack | `MI Basmati Rice - Party Pack` |
| Carrot Slaw - Classic | `MI Carrot Slaw - Classic` |
| Cauliflower + Potato - Classic | `MI Cauliflower + Potato - Classic` |
| Cauliflower + Potato - Party Pack | `MI Cauliflower + Potato - Party Pack` |
| Chicken Tikka - Classic | `MI Chicken Tikka - Classic` |
| Chicken Tikka - Party Pack | `MI Chicken Tikka - Party Pack` |
| Chili Lime Vinaigrette - Classic | `MI House Vinaigrette Dressing - Classic` |
| Chili Lime Vinaigrette - Party Pack | `MI House Vinaigrette Dressing - Party Pack` |
| Coconut Ginger - Classic | `MI Coconut Ginger - Classic` |
| Coconut Ginger - Party Pack | `MI Coconut Ginger - Party Pack` |
| Combo Naan - Large (serves 20) | `MI Combo Naan Basket Large` |
| Cucumber Cubes - Classic | `MI Cucumber Cubes - Classic` |
| Cucumber Cubes - Party Pack | `MI Cucumber Cubes - Party Pack` |
| Ginger Tamarind Chutney - Party Pack | `MI Tamarind Ginger Chutney - Party Pack` |
| Indian Street Corn - Classic | `MI Indian Street Corn - Classic` |
| Indian Street Corn - Party Pack | `MI Indian Street Corn - Party Pack` |
| Kachumber Salad - Classic | `MI Kachumber Salad - Classic` |
| Kachumber Salad - Party Pack | `MI Kachumber Salad - Party Pack` |
| Kokum Vinaigrette Dressing - Classic | `MI Kokum Vinaigrette Dressing - Classic` |
| Kokum Vinaigrette Dressing - Party Pack | `MI Kokum Vinaigrette Dressing - Party Pack` |
| Lamb Kebab - Classic | `MI Lamb Kebab - Classic` |
| Lemon Turmeric Rice - Classic | `MI Lemon Turmeric Rice - Classic` |
| Lemon Turmeric Rice - Party Pack | `MI Lemon Turmeric Rice - Party Pack` |
| Mango Salsa - Classic | `MI Mango Salsa - Classic` |
| Mango Salsa - Party Pack | `MI Mango Salsa - Party Pack` |
| Mint Cilantro Chutney - Classic | `MI Mint Cilantro Chutney - Classic` |
| Mint Cilantro Chutney - Party Pack | `MI Mint Cilantro Chutney - Party Pack` |
| Pickled Onions - Classic | `MI Pickled Onions - Classic` |
| Pickled Onions - Party Pack | `MI Pickled Onions - Party Pack` |
| Roasted Lentils - Classic | `MI Roasted Lentils - Classic` |
| Roasted Vegetables - Classic | `MI Harvest Veggies - Classic` |
| Roasted Vegetables - Party Pack | `MI Harvest Veggies - Party Pack` |
| Romaine Lettuce - Classic | `MI Romaine - Classic` |
| Samosa Tray - 20 Pieces | `MI Samosa Tray - 20` |
| Sauteed Spinach - Classic | `MI Sautéed Spinach - Classic` |
| Sauteed Spinach - Party Pack | `MI Sautéed Spinach - Party Pack` |
| Sexygreens - Classic | `MI Sexygreens - Classic` |
| Sexygreens - Party Pack | `MI Sexygreens - Party Pack` |
| Shredded Paneer Cheese - Party Pack | `MI Shredded Paneer - Party Pack` |
| South Indian Rice Noodles - Party Pack | `MI South Indian Rice Noodles - Party Pack` |
| Spiced Chickpeas - Classic | `MI Spiced Chickpeas - Classic` |
| Spiced Chickpeas - Party Pack | `MI Spiced Chickpeas - Party Pack` |
| Spicy Chili Chicken - Classic | `MI Spicy Chili Chicken - Classic` |
| Sweet Tamarind - Classic | `MI Sweet Tamarind Chutney` |
| Tamarind Chili (Spicy) - Classic | `MI Tamarind Chili - Classic` |
| Tamarind Chili (Spicy) - Party Pack | `MI Tamarind Chili - Party Pack` |
| Tandoori Paneer - Classic | `MI Tandoori Paneer - Classic` |
| Tandoori Paneer - Party Pack | `MI Tandoori Paneer - Party Pack` |
| Tikka Masala - Classic | `MI Tikka Masala - Classic` |
| Tikka Masala - Party Pack | `MI Tikka Masala - Party Pack` |
| Toasted Cumin Yogurt - Party Pack | `MI Toasted Cumin Yogurt - Party Pack` |
| Tomato Garlic (Mild) - Classic | `MI Tomato Garlic - Classic` |
| Tomato Garlic (Mild) - Party Pack | `MI Tomato Garlic - Party Pack` |
| 30 Chai Cookie Basket | `MI Masala Chai Cookies - 30` |
| Arugula - Party Pack | `MI Arugula - Party Pack` |
| Butter Chicken Burrito | `MI Butter Chicken Burrito - In House` |
| Carrot Slaw - Party Pack | `MI Carrot Slaw - Party Pack` |
| Cauliflower + Potato | `MI Cauliflower + Potato` |
| Chicken Tikka | `MI Chicken Tikka` |
| Garlic Naan - Large (serves 20) | `MI Garlic Naan Basket Large` |
| Ginger Tamarind Chutney - Classic | `MI Tamarind Ginger Chutney - Classic` |
| Lamb Kebab - Party Pack | `MI Lamb Kebab - Party Pack` |
| Peanut Sesame - Classic | `MI Peanut Sesame - Classic` |
| Samosa Tray - 50 Pieces | `MI Samosa Tray - 50` |
| South Indian Rice Noodles - Classic | `MI South Indian Rice Noodles - Classic` |
| Tandoori Paneer Burrito | `MI Tandoori Paneer Burrito - In House` |
| Toasted Cumin Yogurt - Classic | `MI Toasted Cumin Yogurt - Classic` |
| Vegan Veggie Burrito | `MI Vegan Veggie Burrito - In House` |
| Arugula - Classic | `MI Arugula - Classic` |
| Basmati Rice | `MI Basmati Rice` |
| Carrot Slaw | `MI Carrot Slaw` |
| Catering - Additional Item - Basmati Rice | `MI Basmati Rice - Catering - Additional Item` |
| Catering - Additional Item - Cauliflower + Potato | `MI Cauliflower + Potato - Catering - Additional Item` |
| Catering - Additional Item - Coconut Ginger | `MI Coconut Ginger - Catering - Additional Item` |
| Catering - Additional Item - Lamb Kebab | `MI Lamb Kebab - Catering - Additional Item` |
| Catering - Additional Item - Peanut Sesame | `MI Peanut Sesame - Catering - Additional Item` |
| Catering - Additional Item - Roasted Vegetables | `MI Harvest Vegetables - Catering - Additional Item` |
| Catering - Additional Item - Sauteed Spinach | `MI Sauteed Spinach - Catering - Additional Item` |
| Catering - Additional Item - South Indian Rice Noodles | `MI South Indian Rice Noodles - Catering - Additional Item` |
| Catering - Additional Item - Spiced Chickpeas | `MI Spiced Chickpeas - Catering - Additional Item` |
| Catering - Additional Item - Tamarind Chili (Spicy) | `MI Tamarind Chili - Catering - Additional Item` |
| Catering - Additional Item - Tikka Masala | `MI Tikka Masala - Catering - Additional Item` |
| Catering - Additional Item - Tomato Garlic (Butter Masala) | `MI Tomato Garlic - Catering - Additional Item` |
| Chopped Cilantro | `MI Chopped Cilantro` |
| Coconut Ginger | `Coconut Ginger` |
| Crispy Chickpea Noodles | `MI Chickpea Noodles` |
| Cucumber Cubes | `MI Cucumber Cubes` |
| Extra Cauliflower + Potato | `MI Cauliflower + Potato` |
| Extra Roasted Vegetables | `MI Harvest Veggies` |
| Ginger Tamarind Chutney | `MI Tamarind Ginger Chutney` |
| Harvest Vegetables - Classic | `MI Harvest Veggies - Classic` |
| Kachumber Salad | `MI Kachumber Salad` |
| Lemon Turmeric Rice | `MI Lemon Turmeric Rice` |
| Masala Quinoa | `MI Masala Quinoa` |
| Masala Quinoa - Classic | `MI Masala Quinoa - Classic` |
| Mint Cilantro Chutney | `MI Mint Cilantro Chutney` |
| Pickled Onions | `MI Pickled Onions` |
| Plain Naan - Large (serves 20) | `MI Plain Naan Basket Large` |
| Roasted Lentils | `MI Roasted Lentils` |
| Roasted Vegetables | `MI Harvest Veggies` |
| Romaine Lettuce - Party Pack | `MI Romaine - Party Pack` |
| Samosa Tray - 100 Pieces | `MI Samosa Tray - 100` |
| Sauteed Spinach | `MI Sautéed Spinach` |
| Spicy Chili Chicken | `MI Spicy Chili Chicken` |
| Tandoori Paneer - * | `MI Tandoori Paneer` |
| Tikka Masala | `MI Tikka Masala Sauce` |
| Toasted Cumin Yogurt | `MI Toasted Cumin Yogurt` |
| Tomato Garlic (Butter Masala) | `MI Tomato Garlic Sauce` |
| Garlic Naan - Small (serves 10) | `MI Garlic Naan Basket Small` |
| Peanut Sesame - Party Pack | `MI Peanut Sesame - Party Pack` |
| Plain Naan - Small (serves 10) | `MI Garlic Naan Basket Small` |
| Samosa Tray - 10 Pieces | `MI Samosa Tray - 10` |
| Catering - Additional Item - Chicken Tikka | `MI Chicken Tikka - Catering - Additional Item` |
| 50 Chai Cookie Basket | `MI Masala Chai Cookies - 50` |
| Baby Spinach - Classic | `MI Baby Spinach - Classic` |
| Shredded Paneer Cheese - Classic | `MI Shredded Paneer - Classic` |
| ZeroCater - Chicken Tikka | `MI Zerocater Chicken Tikka` |
| ZeroCater - Chili Lime Vinaigrette | `MI Zerocater Chili Lime Vinaigrette` |
| ZeroCater - Kachumber Salad | `MI Zerocater Kachumber Salad` |
| ZeroCater - Lamb Kebab Meatballs | `MI Zerocater Lamb Kebab Meatballs` |
| ZeroCater - Lemon Turmeric Rice | `MI Zerocater Lemon Turmeric Rice` |
| ZeroCater - Sautéed Spinach | `MI Zerocater Sautéed Spinach` |
| Combo Naan - Small (serves 10) | `MI Combo Naan Basket Small` |
| Spicy Chili Chicken - Party Pack | `MI Spicy Chili Chicken - Party Pack` |
| Turmeric Ginger Lemonade - 1 Gallon | `MI Turmeric Ginger Lemonade - 1 Gallon` |
| Baby Spinach - Party Pack | `MI Baby Spinach - Party Pack` |
| Catering - Additional Item - Lemon Turmeric Rice | `MI Lemon Turmeric Rice - Catering - Additional Item` |
| Catering - Additional Item - Tandoori Paneer | `MI Tandoori Paneer - Catering - Additional Item` |
| Extra Chicken Tikka | `MI Chicken Tikka` |
| Extra Organic Tandoori Paneer | `MI Tandoori Paneer` |
| Harvest Vegetables - Party Pack | `MI Harvest Veggies - Party Pack` |
| Lamb Kebab | `MI Lamb Kebab Meatballs` |
| Masala Quinoa - Party Pack | `MI Masala Quinoa - Party Pack` |
| Skip Main | `MI Skip Main` |
| Tandoori Paneer | `MI Tandoori Paneer` |
| 100 Chai Cookie Basket | `MI Masala Chai Cookies - 100` |
| Avocado - Classic | `MI Avocado` |
| Catering - Additional Item - Harvest Vegetables | `MI Harvest Vegetables - Catering - Additional Item` |
| Catering - Additional Item - Masala Quinoa | `MI Masala Quinoa - Catering - Additional Item` |
| Roasted Lentils - Party Pack | `MI Roasted Lentils - Party Pack` |
| Golden Ginger Lemonade | `MI Turmeric Ginger Lemonade` |
| Naan | `MI Naan` |
| Mint Limeade | `MI Mint Cardamom Limeade` |
| Avocado | `MI Avocado` |
| HUNGRY Harvest Vegetables | `MI HUNGRY Harvest Veggies` |
| Spicy Chicken Burrito | `MI Spicy Chicken Burrito - In House` |
| Kokum Punch - 1 Gallon | `MI Kokum Punch - 1 Gallon` |
| Mint Cardamom Limeade - 1 Gallon | `MI Mint Cardamom Limeade - 1 Gallon` |
