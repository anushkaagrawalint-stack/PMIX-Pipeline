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
