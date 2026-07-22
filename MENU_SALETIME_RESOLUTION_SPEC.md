# Spec — Sale-time menu resolution ("dashboard matches Toast, always")

*Prepared 2026-07-21 from production data. Owner decision: the dashboard shows each
order under the menu it was rung up on, exactly as Toast's own reports do — menu
restructures must never rewrite order history.*

**For:** Anushka (implementation, `PMIX-Pipeline` repo) · **From:** Rishabh + Claude
(spec + verification) · **Template:** exact change + live-data expected numbers +
validation harness.

---

## 0. What happened (verified end-to-end on prod)

Rahul restructured Toast menus (~Jul 18–20): offsite partner items (Aramark, Eurest,
…) had been listed on **both** `CATERING - 3PD` and `OFFSITE POP-UPS`; his change
**removed them (and their menu groups) from `CATERING - 3PD`**, leaving them only on
the offsite menu. He also relabeled groups (`Aramark*` → `Aramark`, `Fooda` →
`Fooda Catering`).

The Sunday weekly deep re-pull (run 72, Jul 19, window Jun 19 → Jul 19, 21,656
orders) re-parsed that window with the new config. Result: **28 historical lines /
$9,299 for Jul 6–12** (Aramark 23 / $7,629, Eurest 5 / $1,670) flipped
`CATERING - 3PD` → `OFFSITE POP-UPS` in `public.fact_order_lines` — while Toast's
own reports still show them under Catering 3PD (Toast keeps the sale-time stamp).
Caught by the owner's export diff (`attachment db check for dashboard P5.xlsx`);
the orders themselves were never edited in Toast (`modified_date` unchanged).

## 1. Root cause — exact code path

[toast_pipeline/parse/orders.py:86-88](../PMIX-Pipeline/toast_pipeline/parse/orders.py#L86):

```python
item_group_guid = (sel.get("itemGroup") or {}).get("guid", "") or ""
mg = (lookups.get("menu_group_guid", {}).get(item_group_guid)
      or lookups["menu_group"].get(item_guid, {}))
```

The primary path is already sale-time-correct: `sel.itemGroup` is the menu group the
order was actually rung from, and a group belongs to exactly one menu. The bug is
that the lookup is built from the **latest config only** (`cli.py:40` run path builds
from the fresh fetch; `cli.py:133` reparse path uses `db.fetch_latest_config`,
db.py:275 — `DISTINCT ON (config_type) … ORDER BY pull_run_id DESC`).

So when a group is **deleted** from the config (exactly what a menu restructure
does), the group lookup misses and the code silently falls back to the item-guid
lookup — which reflects the item's *current* menu placement. History rewrites
itself, but only inside whatever window happens to get re-parsed → inconsistent
seams (verified: Aramark/Eurest weeks ≤ Jun 8 still Catering 3PD, Jun 15/22 mixed,
Jun 29+ all Offsite).

## 2. The fix — resolve group guids against the snapshot as of the order date

We snapshot the full menu config **daily per location** in `raw.toast_config`
(`config_type = 'menus'`, coverage since **2026-06-12**, 5 locations). Use it.

### 2.1 New helper — `db.fetch_menu_snapshots(conn, location_code)`

Returns the location's menus snapshots as `[(snapshot_date, payload), …]` ascending,
**one per day** (latest `pull_run_id` per `fetched_at::date`), and deduplicated:
drop a day whose payload is byte-identical to the previous kept day (menus change
rarely; this keeps it to a handful of distinct catalogs).

```sql
SELECT DISTINCT ON (fetched_at::DATE) fetched_at::DATE AS snapshot_date, payload
FROM raw.toast_config
WHERE location_code = %s AND config_type = 'menus'
ORDER BY fetched_at::DATE, pull_run_id DESC
```

### 2.2 New builder — `config_api.build_time_lookups(snapshots, latest_cfg)`

For each distinct snapshot build the same two menu lookups `build_lookups` makes
today (`menu_group_guid`, `menu_group` — reuse `_walk_groups` as-is). Return a
resolver used by the parser:

```
resolve(item_group_guid, item_guid, business_date) -> {"menu": …, "group": …}
```

Resolution order (first hit wins):
1. **group guid in the as-of snapshot** — latest snapshot_date ≤ business_date,
   clamped to the earliest snapshot for pre-2026-06-12 dates. (Sale-time truth;
   also restores sale-time group *names* — old rows show `Aramark*`/`Fooda` again,
   matching Toast.)
2. **group guid in the nearest other snapshot** — scan backward from the as-of
   snapshot, then forward. (Covers groups created and deleted between snapshots.)
3. **item guid in the as-of snapshot** (today's fallback, made time-aware).
4. **item guid in the latest config** (current behavior, last resort).

Non-menu lookups (`dining`, `sales_cat`, `alt_pay`, `option_group`) are **out of
scope** — keep building them from the latest config exactly as today.

### 2.3 Call sites

- **run** (`cli.py:33-50` `_pull_location`): after `land_raw_config`, build the
  resolver from `db.fetch_menu_snapshots` (which now includes today's snapshot)
  plus the fresh cfg for the non-menu lookups. Same behavior as today for orders
  whose groups still exist; time-aware for the back-padded days.
- **reparse** (`cli.py:118-141`): replace the single `fetch_latest_config` +
  `build_lookups` pair the same way. Reparse output must no longer depend on
  *when* the reparse is run.
- **parse** (`parse/orders.py:86-88`): swap the two-dict lookup for
  `resolve(item_group_guid, item_guid, business_date)` — `business_date` is already
  in scope. No schema/merge changes; staging and public columns are untouched.

## 3. Rollout

1. Deploy the pipeline change (PR into `anushkaagrawalint-stack/PMIX-Pipeline`).
2. `python -m toast_pipeline.cli reparse --start 2026-06-12 --end <today> --merge`
   — reverts every line the June/July re-pulls mis-stamped.
3. Verify (§4), then run the same reparse again — must produce zero row changes
   (idempotence).

Pre-2026-06-12 history clamps to the earliest snapshot — materially identical to
its current stamps (those orders were imported with June/July configs anyway);
a full-history reparse is optional and safe but not required.

## 4. Acceptance (live-data, captured 2026-07-21)

1. **The 28 flipped lines revert to `CATERING - 3PD`.** Selection list +
   sale-time group-guid proof: prototype resolved **28/28** to the sale-time menu
   (validation harness: [group-asof-validate.mjs](group-asof-validate.mjs); line
   list: [flipped28.csv](flipped28.csv) — from the owner's xlsx diff; both in this
   docs folder). After reparse:
   ```sql
   SELECT COUNT(*) FROM public.fact_order_lines
   WHERE selection_guid = ANY(<flipped28 list>) AND menu_name = 'CATERING - 3PD';
   -- expect 28
   ```
2. **Jul 6–12 ties Toast again**: Aramark+Eurest-paid lines that week show $9,299
   under Catering 3PD and $0 newly-Offsite (cross-check against the Toast UI
   export in the owner's workbook, `item_details` sheet).
3. **Revenue invariance**: total `SUM(line_total) WHERE NOT is_voided` per
   location × business_date over Jun 12 → today is identical before/after the
   reparse (only menu_name/menu_group/channel move).
4. **Mid-June seam heals**: weeks of Jun 15 / Jun 22 (currently mixed: $3,885 /
   $4,344 shown Offsite) re-stamp per sale-time groups. Expected values are
   whatever the harness computes — run `group-asof-validate.mjs` extended over
   Jun 12 → today *before* the reparse and diff after; every line must match its
   sale-time group's menu.
5. **Forward behavior**: once RASA's team starts entering offsites on the new menu
   (Rahul: "next week onwards"), those orders resolve `OFFSITE POP-UPS` via the
   primary group-guid path — spot-check the first such order.
6. **No dashboard changes needed** — channel derives from `menu_name` and
   `analytics.channel_overrides` still COALESCEs on top. The `pc_*` precompute
   refresh runs inside `merge_to_public()` and picks the reparse up automatically.

## 5. Notes & consequences

- **This supersedes the "restate history to the new structure" option** discussed
  before the mechanism was understood. Policy locked with the owner: match Toast.
  The Aramark→Offsite shift will appear in the dashboard from the date the team
  actually starts using the new menu — same date it appears in Toast's reports.
- The **dual-listing tie-break** (item on several menus, dict last-wins) still
  exists in fallbacks 3/4 but only fires when the sale-time group is unknown —
  rare, and no worse than today.
- **Snapshot gaps**: config snapshots exist only on days a pull ran. The as-of
  rule (latest ≤ date) bridges gaps correctly; changes made mid-gap resolve to the
  next snapshot's view — unavoidable, immaterial.
- `raw.toast_config` retention matters now: menus snapshots are the source of
  sale-time truth. Never prune them (raw is sacred anyway).
- The owner's meeting line to Rahul stays as agreed, plus one addition: *"the brief
  window where last week's Aramark showed as Offsite on our dashboard will be
  corrected back — we now pin every order to the menu structure of its sale date."*
