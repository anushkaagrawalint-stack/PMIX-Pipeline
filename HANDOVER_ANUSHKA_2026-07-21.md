# Handover — Aramark / Offsite vs Catering 3PD attribution fix (2026-07-21)

Hi Anushka — one pipeline change, verified end-to-end on production before writing
this. Full spec with exact code paths and acceptance SQL is attached
(`MENU_SALETIME_RESOLUTION_SPEC.md`); this page is the summary and rollout order.

**Repo:** `PMIX-Pipeline` · **Urgency: high** — the data drifts a little more every
Sunday until this lands.

## What happened

Rahul restructured the Toast menus on ~Jul 18–20: offsite-partner items (Aramark,
Eurest, …) had been listed on **both** `CATERING - 3PD` and `OFFSITE POP-UPS`; he
removed them (and their menu groups) from `CATERING - 3PD`, and relabeled some
groups (`Aramark*` → `Aramark`, `Fooda` → `Fooda Catering`).

Sunday's weekly 30-day deep re-pull (run 72, Jul 19) re-parsed Jun 19 → Jul 19 with
the new config. Result: **28 historical lines / $9,299 in the Jul 6–12 week
flipped** from Catering 3PD to Offsite in `public.fact_order_lines` — so the
dashboard stopped matching Toast's own reports for that week (Toast keeps the
sale-time stamp; the orders themselves were never edited — verified via
`modified_date`). Earlier weeks show a seam: ≤ Jun 8 all Catering 3PD, Jun 15/22
mixed, Jun 29+ all Offsite.

**Rishabh's decision (locked):** the dashboard must always match Toast — every
order shows under the menu it was rung up on. Menu restructures must never rewrite
order history.

## Root cause — your parser is already 90% right

[parse/orders.py:86-88] resolves menu via the selection's `itemGroup` guid — that IS
sale-time information (a group belongs to exactly one menu), which is why our
history matched Toast until now. The bug: the `menu_group_guid` lookup is built
from the **latest config only** (run path `cli.py:40`; reparse path `cli.py:133` →
`db.fetch_latest_config`, db.py:275). When a restructure **deletes** a group, the
lookup misses and silently falls back to the item-guid → the item's *current* menu
placement. Any re-parsed window re-stamps history.

## The fix (spec §2)

We already snapshot the full menus config daily per location in `raw.toast_config`
(since 2026-06-12). Build the group lookup from those snapshots and resolve **as of
each order's `business_date`**:

1. group guid in the as-of snapshot (latest snapshot ≤ business_date, clamped to
   earliest for older dates);
2. group guid in the nearest other snapshot (backward, then forward);
3. item guid in the as-of snapshot;
4. item guid in the latest config (today's behavior, last resort).

Touches: `db.py` (new `fetch_menu_snapshots` — one snapshot per day, deduped),
`config_api.py` (time-aware builder reusing `_walk_groups` as-is),
`parse/orders.py:86-88` (`business_date` is already in scope), and both call sites
in `cli.py`. Non-menu lookups (dining, sales categories, alt-pay, option groups)
stay exactly as they are. No schema or merge changes; **no dashboard changes** —
channel derives from `menu_name` and overrides still COALESCE on top.

**Proof the design works:** we resolved all 28 flipped lines through their
sale-time group guid against the as-of snapshots — **28/28 come back
`CATERING - 3PD`**, matching Toast. Harness + line list attached
(`group-asof-validate.mjs`, `flipped28.csv`).

## Rollout — order matters

1. Merge the pipeline change.
2. `python -m toast_pipeline.cli reparse --start 2026-06-12 --end <today> --merge`
   — reverts the flips and heals the mixed mid-June weeks (`pc_*` precompute
   refreshes itself inside the merge).
3. Run the same reparse again — must change **zero** rows (idempotence check).
4. Acceptance (spec §4): the 28-line revert query; Jul 6–12 Aramark+Eurest =
   $9,299 back under Catering 3PD; revenue totals per location × day unchanged;
   first order entered on the new offsite menu (RASA's team starts "next week")
   resolves to `OFFSITE POP-UPS` via the primary group path.

## Notes

- Pre-2026-06-12 orders clamp to the earliest snapshot — materially identical to
  their current stamps; full-history reparse optional.
- `raw.toast_config` menus snapshots are now the source of sale-time truth —
  never prune them.
- Going forward, the Aramark/offsite shift will appear in the dashboard from the
  date the team actually starts using the new menu — the same date it appears in
  Toast's reports. That's the intended behavior, agreed with Rahul's side.

Questions → Rishabh, or reply on the PR. The spec contains all validation SQL, so
acceptance is checkable without us in the loop.
