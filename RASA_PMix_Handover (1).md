# RASA PMix Pipeline — Project Handover & Context Pack

**Owner handing over:** Rishabh Srivastav (Kutlerri)
**Prepared:** June 15, 2026
**Status at handover:** Phase 1 (Toast → Neon ingestion) functionally complete. Phase 2 (cost + Menu Engineering layer) not started.

---

## 0. HOW TO USE THIS DOCUMENT

This document does two jobs at once: it's a human briefing for whoever takes the project over, **and** it's a context pack you paste into a Claude chat so Claude can resume exactly where the previous work stopped.

**To resume the work with Claude:**
1. Open a new Claude chat (ideally in the same Project workspace if you have access).
2. Paste this entire document as your first message, with a line at the top like:
   *"I'm taking over the RASA PMix pipeline from Rishabh. Below is the full handover. Please confirm you've understood the current state, then help me with [your task]."*
3. When you need Claude to work on the actual code, **upload the `pmix-pipeline` repo** (zip it and attach, or paste specific files). Claude can read and edit the files but does not have them until you provide them.
4. For anything touching the live database or Toast, you run the commands locally — Claude can't reach your machine, Neon, or Toast directly. You paste outputs back; Claude interprets and gives next steps.

**Golden rules carried over from the project:**
- **Toast is the source of truth, not anyone's dashboard.** The client has their own parallel system; we validate *against Toast's own Sales Summary*, not against their numbers.
- **Cleaning decisions are reviewable CSV edits, never silent code changes.** A menu item or modifier is never merged/relabeled without sign-off.
- **The `raw` schema is sacred.** Every correction is applied by *reparsing* existing raw data, never by re-downloading from Toast. This has saved hours repeatedly.
- **Check project context before flagging something as a bug.** Several "bugs" in this project turned out to be intentional design (e.g. null costs for modifier-dependent items). Confirm before assuming.

---

## 1. PROJECT CONTEXT

**Who:** Kutlerri (Rishabh's analytics brand) provides Menu Engineering analytics to **RASA**, a five-location DC-area Indian restaurant group.

**The five locations** (code → name → address → Toast GUID):

| Code | Name | Address | Toast Restaurant GUID |
|---|---|---|---|
| BALLPARK | Ballpark | 1247 First St SE | `79d51474-ed4d-4f69-bc16-f223af237c87` |
| MVT | MVT (Mount Vernon Triangle) | 485 K Street NW | `4b9516cc-7808-4ddd-8235-b8d04f0b0373` |
| NL | NL (National Landing) | 2200 Crystal Drive Ste F | `c476d92d-0704-400f-9db1-fddef01950ec` |
| MOSAIC | Mosaic | 2905 District Ave #160 | `259deaa2-fc2b-4b88-9483-5298e7cc445d` |
| ROCKVILLE | Rockville | 12033 Rockville Pike | `6c83054e-3d2d-4ad6-8d80-f6e6d86f01b3` |

**The deliverable (the "why"):** a monthly **PMix (Product Mix) / Menu Engineering report** that classifies every menu item into one of four quadrants — **Star, Plow Horse, Puzzle, Dog** — based on popularity (menu mix %) and profitability (margin %), broken down by sales channel. This drives decisions about which items to reprice, reposition, promote, or cut.

**History of the build:**
- Originally a manual workbook (the February 2026 `PMix_Automation_Template.xlsx` remains the authoritative reference for cost logic).
- Then a Google Apps Script (GAS) pipeline (`PMIX_Pipeline_v7.gs`) processing Toast exports → Google Slides deck.
- The **client independently built their own** Toast → Supabase → Cloudflare dashboard (sales/product-mix only — no cost, margin, Menu Engineering, or retention).
- **Strategic decision:** build the "best of both" — adopt the client's superior data-engineering approach (direct Toast API, star schema, daily refresh) but keep Kutlerri's unique value (the cost → margin → Menu Engineering → retention layer). Because the client will **not** grant access to their codebase / Supabase / Cloudflare, we build a **parallel, Kutlerri-owned stack from scratch.** That is the `pmix-pipeline` project this document hands over.

---

## 2. TARGET ARCHITECTURE (the full vision)

```
Toast POS API ─┐
R365 costs ────┼─► Neon Postgres ─► Kutlerri analytics layer ─► Dashboard + monthly report
Bikky retention┘   (raw→staging→public   (cost engine, ME MVs,
                    star schema)           retention) [analytics schema]
```

- **Extraction:** direct Toast API, all 5 locations, daily — no manual exports. **(BUILT — Phase 1)**
- **Storage:** Neon Postgres, three-schema trust boundary. **(BUILT — Phase 1)**
- **Analytics layer:** R365 cost ingest, pink-sheet cost logic in SQL, `mv_menu_engineering`, Bikky retention. **(NOT BUILT — Phase 2)**
- **Serving:** Kutlerri's existing Next.js dashboard (repo `PMIX-Dashboard`) rewired to read Postgres instead of xlsx uploads, plus new Menu Engineering / Margins / Retention tabs. **(NOT BUILT — Phase 3)**

---

## 3. WHAT EXISTS RIGHT NOW — the `pmix-pipeline` repo

A Python + SQL project. Runs locally today; designed to run on GitHub Actions daily (not yet activated).

```
pmix-pipeline/
  sql/
    001_init.sql            schemas (raw/staging/public/analytics), raw landing tables, pull_runs tracking
    002_staging.sql         staging tables (rebuilt every run)
    003_public_dims.sql     dim_location, dim_item, dim_modifier, dim_channel, channel_override
    004_public_facts.sql    fact_order_lines, fact_modifiers, fact_checks, br_order_payment, fact_adjustments
    005_merge_to_public.sql  staging→public upsert + channel attribution logic
  toast_pipeline/
    config.py               env loading, Location dataclass, constants
    auth.py                 Toast OAuth2 (client-credentials), token caching
    db.py                   Neon connection, raw landing (batched), staging COPY, merge, reparse helpers
    cli.py                  command-line entry: init-db / run / reparse / merge / validate
    fetch/orders.py         ordersBulk pagination + rate limiting + businessDate attribution
    fetch/config_api.py     dining options / menus / sales categories / alt payment types lookups
    parse/orders.py         order → checks → selections → modifiers (recursive) parser
    clean/normalize.py      name canonicalization, encoding repair, CSV-driven rules
  mappings/                 ← THE REVIEWABLE RULEBOOK (edit these, then `reparse`)
    name_mappings.csv       raw display name → canonical name (renames, cross-menu variants)
    modifier_blocklist.csv  modifier pollution to exclude (utensils, napkins…)
    modifier_tags.csv       modifier canonical name → type (base/sauce/veggie/main/…)
    mix_exclusions.csv      items excluded from menu-mix/ME math (markups, fees, gift cards)
  scripts/
    check_reconciliation.py  staging↔public drift + per-location revenue report
    list_locations.py        discover/confirm location GUIDs from Toast
  .github/workflows/
    daily_pipeline.yml      daily cron (11:00 UTC) + manual backfill — NOT YET ACTIVATED
  .env                      secrets (NOT in git) — Toast creds, location GUIDs, Neon URL
  .env.example              template
  requirements.txt          requests, psycopg[binary], python-dotenv
  README.md                 technical readme + acceptance checklist
```

**The data model in one paragraph:** `fact_order_lines` is the spine — one row per selection (line item) per check, with `canonical_name`, `channel_code`, `quantity`, `line_total`, `is_voided`, `business_date`, `location_code`. Modifiers (recursive, any depth) live in `fact_modifiers` keyed to their parent selection. Payments (with `alt_payment_name`, the key to channel attribution) live in `br_order_payment`. Voids are **kept** with an `is_voided` flag — every revenue query must include `WHERE NOT is_voided`.

---

## 4. CURRENT DATA STATE (as of June 15, 2026)

- **Loaded & cleaned:** Feb 1 – Jun 11, 2026, all five locations.
- **Volume:** ~93,000 orders, ~218,000 line items, ~$2.73M non-void item sales (full Feb–Jun history).
- **Data stops June 11** — today is June 15, so it is ~4 days stale. Activating the daily automation (Open Item #1) will catch it up and keep it current.
- **Quality gates passed:** zero orphan modifiers, zero lines missing a channel, staging↔public drift = OK (0.00), split-name check clean (only legitimate hyphenated beverages remain).

**Cross-validation against the client's own dashboard** (window May 15 – Jun 11, the same range their screenshots showed) — two independently built systems agree on the shape of the business:

| Channel (our attribution) | Our share | Client dashboard share |
|---|---|---|
| In-House | 41% | 46% |
| Catering | 22% | 20% |
| 3PD | 14% | 17% |
| App | 11.5% | 6.4% ⚠ |
| Offsites | 11% | 10% |

Per-location net item sales (May 15–Jun 11): MVT $171,575 · Mosaic $106,865 · NL $106,761 · Rockville $101,562 · Ballpark $82,217. Same ranking and proportions as the client's chart.

Revenue reconciliation (same window): all-items $568,981 → real-menu-only (after exclusions) $545,458 vs client dashboard $521,669. The residual ~4% gap is **definitional** (their "real menu items only" toggle strips additional excluded-category items like the Tandoori Tasting Bundle and event/group items) — not a data error. **This is expected and accepted.** The formal certification is the Toast Sales Summary tie-out (Open Item #3), not the client's number.

⚠ **The one number to revisit:** our **App channel is ~11.5% vs their 6.4%.** Our App attribution rule is the crudest (matches menu names containing "app"/"online") and may be over-capturing. To be resolved during the Phase-2 cross-check against the GAS pipeline's Clean Menu Breakdown (where LOYALTY = APP + Toast Online Ordering is the known reference).

---

## 5. NON-NEGOTIABLE TECHNICAL RULES (carry these forward exactly)

These were established by reverse-engineering the manual workbook and the client's architecture. Do not change them without understanding why they exist.

**Toast extraction semantics:**
- `ordersBulk` `startDate`/`endDate` filter on the order's **modified** timestamp, NOT business date. We over-fetch a padded window and attribute every order to `payload.businessDate` (local service day), deduping on `order_guid`. Never attribute by UTC timestamps.
- **Dining-option names are null** on the order payload — resolved via `/config/v2/diningOptions`. The run **HALTS** if that lookup is empty (otherwise channel attribution silently breaks).
- `appliedMenu` is **always null** on bulk orders — menu / menu-group is resolved at load time from `/menus/v2/menus` (which also keeps `dim_item.menu_group` current).
- Rate limit is **5 req/s per location**; one worker per location keeps quotas independent.

**Revenue definition:**
- **Revenue = `SUM(line_total) WHERE NOT is_voided`** — gross, pre-tax, pre-discount, **excludes service charges.** This differs from Toast UI "Net Sales" by exactly the service-charge total (and discounts depending on the view). That delta is expected, not a bug.

**Channel attribution** (in `005_merge_to_public.sql`, COALESCE'd under manual `channel_override`):
- alt-payment name matches EzCater/HUNGRY/Sharebite/Territory/Cater2Me/ZeroCater/CaterCow/WCK/FoodFleet → **CATERING**
- alt-payment matches Fooda/Aramark/Eurest/Metz/Taher/Foodworks/Cureate/GuestServices → **OFFSITE**
- alt-payment matches DoorDash/Uber/GrubHub/Postmates → **TPD** (3rd-party delivery)
- dining option / sales category contains "catering" → **CATERING**
- menu name contains "online"/"app" → **APP**
- else → **IN_HOUSE**

**Menu Engineering math** (for Phase 2 — confirmed from the manual workbook):
- **Menu Mix High threshold:** `(1/n) × 0.7`, strict `>` comparison, where `n` = count of *included* menu items (mix exclusions must NOT be counted in `n`).
- **Margin High threshold:** `SUM(Total Margin $) / SUM(Net Sales $)`, strict `>` — sales-weighted, NOT arithmetic mean or median.
- **Overall** classification inherits from the channel masters; it does not reclassify independently.
- Quadrants: high mix + high margin = **Star**; high mix + low margin = **Plow Horse**; low mix + high margin = **Puzzle**; low mix + low margin = **Dog**.

**Cost rules (Phase 2 — from the manual workbook, do not "fix" these):**
- **Null Avg Cost for modifier-dependent items is intentional by design** (Side of Main, Side of Grain, Side of Veggie, Side of Sauce, Homemade Juice).
- Base section costs ARE included for Set Plate items; `1/2 Base` should never render as a standalone section.
- TCM = Gross − TC; AvgPrice = Gross/Qty (confirmed against Feb baseline).
- 3PD `×1.22` price multiplier likely retired (real 3PD prices are in the order payload); the `×1.18` cost surcharge stays as a business rule — **verify against the Feb baseline before finalizing.**

---

## 6. CREDENTIALS & ACCESS

| Thing | Where | Notes |
|---|---|---|
| Toast API | Toast Web → Integrations → API access. Credential name **"Kutlerri-PMix"** | `TOAST_MACHINE_CLIENT`, host `ws-api.toasttab.com`, 5 locations, 13 scopes (orders/config/menus/restaurants read). Client ID: `rqYdJeFlbpZFeRYWhmjZ2FEvX82GHQ1H` |
| Toast Client Secret | In the local `.env` only | **SECRET — not in this doc, not in git.** See Open Item #2: must be rotated. |
| Neon Postgres | Kutlerri-owned **separate project** (Launch plan), database `neondb` | Connection string in local `.env` as `DATABASE_URL`. **Password must be rotated — see Open Item #2.** |
| R365 (Restaurant365) | Kutlerri has access | Cost data source for Phase 2. Recipe export files already on hand (`Recipe.csv`, `RecipeItems_20260112.csv`). |
| Bikky | Kutlerri has access | Loyalty/CRM; 90-day return & reorder rates per item, for Phase 2 retention layer. |

**The `.env` file** (lives in the repo folder, excluded from git) has five lines:
```
TOAST_HOST=https://ws-api.toasttab.com
TOAST_CLIENT_ID=rqYdJeFlbpZFeRYWhmjZ2FEvX82GHQ1H
TOAST_CLIENT_SECRET=<secret — rotate this>
LOCATIONS=BALLPARK:Ballpark:79d51474-ed4d-4f69-bc16-f223af237c87,MVT:MVT:4b9516cc-7808-4ddd-8235-b8d04f0b0373,NL:NL:c476d92d-0704-400f-9db1-fddef01950ec,MOSAIC:Mosaic:259deaa2-fc2b-4b88-9483-5298e7cc445d,ROCKVILLE:Rockville:6c83054e-3d2d-4ad6-8d80-f6e6d86f01b3
DATABASE_URL=<neon connection string — rotate password>
```

---

## 7. HOW TO OPERATE IT (runbook)

All commands run from inside the `pmix-pipeline` folder with the virtual environment active (`source .venv/bin/activate` on Mac → you'll see `(.venv)` in the prompt).

```bash
# One-time: create all database tables (safe to re-run)
python -m toast_pipeline.cli init-db

# Daily incremental pull (yesterday, back-padded 2 days to catch late voids/edits)
python -m toast_pipeline.cli run

# Backfill a specific window (all locations, or add --locations BALLPARK)
python -m toast_pipeline.cli run --start 2026-06-01 --end 2026-06-11

# Rebuild from raw data already in the DB after editing a mappings CSV — NO Toast calls
python -m toast_pipeline.cli reparse --start 2026-02-01 --end 2026-06-11 --merge

# Finish a run whose pull succeeded but merge failed
python -m toast_pipeline.cli merge

# Print row counts, revenue, orphans, missing-channel checks
python -m toast_pipeline.cli validate

# Reconciliation report (drift + per-location revenue)
python scripts/check_reconciliation.py --start 2026-05-15 --end 2026-06-11

# Confirm/discover location GUIDs (also a quick credential test)
python scripts/list_locations.py
```

**The everyday workflow for fixing a data issue** (this is the loop used dozens of times in setup):
1. Spot an issue in Neon (e.g. a duplicate/split item name, or pollution in the mix).
2. Edit the relevant CSV in `mappings/` (opens in Excel/Numbers). For a rename, add a row to `name_mappings.csv`; for pollution, add to `mix_exclusions.csv` or `modifier_blocklist.csv`.
3. Run `reparse --start … --end … --merge` over the affected window.
4. Re-run the diagnostic query in Neon to confirm.
No re-downloading from Toast, ever — the raw layer makes this possible.

**Useful Neon SQL Editor queries:**
```sql
-- Revenue by channel (a given window)
SELECT channel_code, count(*) lines, round(sum(line_total),2) sales
FROM public.fact_order_lines
WHERE NOT is_voided AND business_date BETWEEN '2026-05-15' AND '2026-06-11'
GROUP BY 1 ORDER BY sales DESC;

-- Top items
SELECT canonical_name, sum(quantity) qty, round(sum(line_total),2) sales
FROM public.fact_order_lines WHERE NOT is_voided
GROUP BY 1 ORDER BY sales DESC LIMIT 20;

-- Split-name check (should return only legit hyphenated beverages)
SELECT canonical_name, count(DISTINCT location_code) locs, sum(quantity) qty
FROM public.fact_order_lines
WHERE NOT is_voided AND canonical_name ILIKE '%- %'
GROUP BY 1 ORDER BY qty DESC LIMIT 15;
```

---

## 8. OPEN ITEMS (do these next — none block Phase 2, but #1 is timely)

**1. Activate the daily automation (HIGH — data is going stale).**
Push the `pmix-pipeline` folder to a **private** GitHub repo (GitHub Desktop is the no-command-line way). Then in the repo: Settings → Secrets and variables → Actions → add four secrets with these exact names: `TOAST_CLIENT_ID`, `TOAST_CLIENT_SECRET`, `LOCATIONS`, `DATABASE_URL` (same values as `.env`). The included `.github/workflows/daily_pipeline.yml` then runs daily at 11:00 UTC and keeps the data current. The `.env` itself is never uploaded (gitignored); GitHub uses the secrets. Verify under the repo's "Actions" tab — green tick = success.

**2. Rotate exposed credentials (SECURITY — do once, soon).**
The Toast client secret and the Neon password both appeared in screenshots during setup. Rotate both:
- Toast Web → Kutlerri-PMix credential → **Rotate** → paste new secret into `.env` and the GitHub secret.
- Neon → project → Connect panel → **Reset password** → update `DATABASE_URL` in `.env` and the GitHub secret.

**3. Toast Sales Summary tie-out (the formal Phase-1 sign-off).**
In Toast Web, pull the Sales Summary for **Ballpark, June 1–7, 2026.** Compare its Net Sales to our figure of **$20,484.69** for the same location-week. The delta should equal service charges + discounts exactly (our number excludes both by design). Document the reconciliation. This is the last unchecked box on the Phase-1 acceptance list.

---

## 9. PHASE 2 ROADMAP — the cost & Menu Engineering layer (the real prize)

This is what turns the sales database into the margin engine the whole project exists for. Lands in a dedicated `analytics` schema (already created by `001_init.sql`) so it evolves independently of ingestion.

1. **R365 cost ingest.** Build `analytics.dim_recipe_cost` (period-versioned). Ingest the R365 recipe-cost exports (`Recipe.csv` / `RecipeItems_*.csv`). Manual file upload first; API later.
2. **Cost engine in SQL.** Port the manual workbook's pink-sheet logic: modifier-weighted average cost per item per channel, the base-section patterns, Set Plate base inclusion, catering modifier filtering. Runs over `public.fact_modifiers` (which captures every modifier at every nesting depth — a superset of the old Clean Modifiers export). **Validate item-by-item against the P3 pink sheets and the Feb baseline.**
3. **Menu Engineering MVs.** Build `analytics.mv_menu_engineering` computing per channel × period (× location — a new capability): menu mix vs `(1/n)×0.7` strict `>`, margin vs `SUM(margin$)/SUM(sales$)` strict `>`, quadrant assignment, Overall inheriting from channels. Honor `mix_exclusions.csv` when counting `n`.
4. **Bikky retention.** `analytics.fact_retention` (period grain) with a **date-staleness guard at ingest** (reject a Bikky file whose period doesn't match — this was a recurring real bug: P1 data showing up where P3 was expected) and a **keyed join** to canonical names (not row-position lookups — the other recurring Bikky bug).
5. **Cross-check the new ME output against the existing GAS pipeline** for one full period, classification-by-classification. The GAS pipeline is the regression oracle for its own replacement. Resolve the App-channel discrepancy here.

**Phase 3 (after Phase 2):** rewire Kutlerri's Next.js dashboard (`PMIX-Dashboard` repo) to read these Postgres MVs instead of xlsx uploads, then add the Menu Engineering / Margins / Retention tabs that the client's own dashboard lacks.

---

## 10. CAVEATS & GOTCHAS (hard-won — don't relearn these the hard way)

- **BYO build data is online-only.** In-house orders record only the protein (main); Base/Sauce/Veggie/Topping/Chutney are captured only on App + 3PD orders. So BYO modifier-weighted costs can't be built from in-house modifier data — the full-build composition is an online-channel proxy. Decide explicitly whether online build mix is an acceptable proxy for in-house costing.
- **TextEdit / paste gotchas (Mac):** `.env` must be plain text (TextEdit: Format → Make Plain Text) and named exactly `.env` (it saves as `.env.txt` by default — rename with `mv`). Pasting into the terminal can prepend an invisible `[200~` (bracketed-paste) marker — type commands by hand or run `printf '\e[?2004l'` once per session.
- **`staging lines` < `public lines` is normal.** Staging holds the current reparse window; public is cumulative across all pulls. The `[OK]` drift check is what matters.
- **There are ~300 rows in `public` from before Feb 1** that fall outside even the widest reparse window run so far. If a stray old-name row ever appears, widen the reparse start date to cover it, or do a targeted SQL `UPDATE`.
- **Beverage flavors look like split names but aren't.** "Spindrift - Lemon", "Olipop - Cola", "Wild Kombucha - Ginger", "LaCroix - Lime" are distinct products and must never be merged. The suffix-stripper only matches an explicit whitelist (`club feast|gameday|side|catering|in house|in-house|ezcater`), never generic dashes — keep it that way.
- **Don't trust "unattrib = 0" alone.** Unmatched lines default to IN_HOUSE, so zero unattributed is guaranteed by construction. The real attribution check is the channel-breakdown query against expectations.

---

## 11. GLOSSARY

- **PMix** — Product Mix; the monthly menu-performance report.
- **Menu Engineering / ME** — Star/Plow Horse/Puzzle/Dog classification by popularity × profitability.
- **Channels** — IN_HOUSE, APP (formerly THANX/LOYALTY = App + Toast Online Ordering), TPD (3rd-party delivery: DoorDash/Uber/GrubHub), CATERING, OFFSITE.
- **3PD** — third-party delivery. **TPD** is the channel code for it in this codebase.
- **BYO** — Build Your Own (bowls/burritos with main + base + sauce + veggie + toppings).
- **Pink sheet** — the per-item cost worksheet in the manual workbook; final cost row is labelled `FINAL AVG COST WITH MODIFIER`.
- **TCM** — Total Contribution Margin (Gross − Total Cost). **TC** — Total Cost.
- **R365** — Restaurant365, the cost data source. **Bikky** — loyalty/CRM, the retention data source.
- **GAS pipeline** — the legacy Google Apps Script pipeline (`PMIX_Pipeline_v7.gs`) being replaced.
- **reparse** — rebuild staging+public from raw payloads already in the DB, applying current cleaning rules, with zero Toast API calls.
- **Period (P1–P13)** — RASA's fiscal reporting calendar (e.g. P3 = March 2026).

---

*End of handover. The previous build session took the project from nothing to a validated, five-location Toast→Neon pipeline with ~$2.73M of audited data. The next session's job: activate automation, then build the cost and Menu Engineering layer on top.*
