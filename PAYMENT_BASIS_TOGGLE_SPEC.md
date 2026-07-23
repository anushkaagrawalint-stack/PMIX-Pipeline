# Spec — Payment date-basis toggle (event date ↔ paid date) + status filter

*Prepared 2026-07-23. Follow-up to `PAYMENT_STATUS_SPEC.md` (done & verified).
Owner decisions: (1) the payments table carries BOTH dates, and the Payment
Source tab gets a toggle between the two views; (2) payment status becomes a
user-facing FILTER on the tab — default CAPTURED + AUTHORIZED, with DENIED and
VOIDED selectable on demand.*

**For:** Anushka · **Repos:** PMIX-Pipeline (part A) + PMIX-Dashboard (part B)

## 0. Why

`br_order_payment.business_date` is the **order's** business date (service/event
day — catering deposits land on the event date). Toast's own Payments report keys
on the **payment's** business date (the day the money moved). Both views are
legitimate: event basis matches revenue attribution; paid basis matches Toast's
report and answers "what came in this week?". Today we only store the first.
Design validated on prod raw: paid-basis Jul 6–12 (CAPTURED+AUTHORIZED) computes
**4,818 payments / $177,575.32 — Toast's export to the cent, zero adjustments.**

## 1. Part A — pipeline: add `paid_business_date`

Payload field: `paidBusinessDate` on every check payment (int `YYYYMMDD`, same
encoding as the order's `businessDate`). Same recipe as `paid_status`:

1. Migration `016_paid_business_date.sql` (idempotent):
   ```sql
   ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS paid_business_date INT;
   ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS paid_business_date DATE;
   ```
2. `parse/orders.py` payments block:
   `"paid_business_date": pay.get("paidBusinessDate"),`
3. `db.py`: add to the staging COPY column list.
4. `005_merge_to_public.sql`: add to the INSERT
   (`to_date(p.paid_business_date::text,'YYYYMMDD')`, NULL-safe: wrap in
   `CASE WHEN p.paid_business_date IS NULL THEN NULL ELSE … END`) and to the
   `DO UPDATE SET` list.
5. Backfill: `reparse --start 2025-12-19 --end <today> --merge`.
   (This also sweeps the 637 NULL `paid_status` rows from the previous rollout —
   two birds.)
6. Index for the new filter path:
   `CREATE INDEX IF NOT EXISTS idx_bop_paid_bd ON public.br_order_payment (paid_business_date);`

## 2. Part B — dashboard: the toggle

1. **Queries** (`getPayments`, `getPaymentsByLocation`, and the payments-KPI
   query): accept two params —
   - `basis: 'event' | 'paid'` — the only change is the date column in WHERE:
     - `'event'` → `business_date BETWEEN $1 AND $2` (today's behavior, default)
     - `'paid'`  → `COALESCE(paid_business_date, business_date) BETWEEN $1 AND $2`
       (COALESCE = safety for any not-yet-backfilled row; after the reparse it
       should never fire).
   - `statuses: string[]` — replaces the hardcoded
     `COALESCE(paid_status,'CAPTURED') NOT IN ('DENIED','VOIDED')` with
     `COALESCE(paid_status,'CAPTURED') = ANY($3)`. Default
     `['CAPTURED','AUTHORIZED']` (identical behavior to today).
   Refund columns identical in all variants.
2. **Data loading**: statuses × basis is too many variants to pre-fetch — move
   the tab to fetch-on-change: keep the default dataset (event basis,
   CAPTURED+AUTHORIZED) in `loadDashboardData` so first paint is unchanged, and
   add one small API route (e.g. `/api/payments?start&end&basis&statuses`,
   same auth pattern as `/api/review/*`) the tab calls when either control
   changes. Show the tab's existing loading state during refetch.
3. **UI** (`PaymentSource` tab header), mirroring existing control styling:
   - **Basis toggle** (segmented, 2 options):
     - **“Service date”** (default) — subtext: *“Payments on the order’s
       business date — catering on the event date. Matches revenue attribution
       on all other tabs.”*
     - **“Paid date”** — subtext: *“Payments on the day the money was
       collected. Matches Toast’s Payments report.”*
   - **Status filter** (multi-select chips or dropdown, 4 options:
     Captured · Authorized · Denied · Voided). Default: Captured + Authorized
     pre-selected. Empty selection is invalid — keep at least one (disable
     unchecking the last).
   Everything on the tab (KPI cards, split donut, top-sources bar, by-location
   bars, sources table) re-renders from the fetched dataset. Component state
   only; reset to defaults on reload is fine.
4. **Label honesty**:
   - When 'paid' basis is active: one caption line noting catering deposits
     appear on collection dates.
   - When Denied and/or Voided are selected: a visible warning caption, e.g.
     *“Includes N denied/voided payments ($X) — money that was never
     collected.”* The KPI cards must not silently blend uncollected money
     without this cue.

## 3. Acceptance (live numbers, captured 2026-07-23)

All for the week Jul 6–12 unless stated; owner holds the matching Toast export
(`Payment_Toast` sheet in `attachment db check for dashboard P5 (1).xlsx`).

| Basis | Statuses | Expected | Ties to |
|---|---|---|---|
| Service date (default) | Captured+Authorized (default) | **4,815 / $176,526.19** | already-verified live numbers — must not change |
| Paid date | Captured+Authorized | **4,818 / $177,575.32** | Toast export filtered to captured, to the cent |
| Paid date | all four | **4,852 / $178,407.51** | Toast export UNfiltered, to the cent |
| Service date | all four | **4,849 / $177,358.38** | pre-fix tab total (the old unfiltered behavior) |

Plus:
1. Post-backfill, zero rows where `paid_business_date IS NULL` (all-time), and
   zero remaining NULL `paid_status`.
2. Sanity identity over the full data range: event-basis total = paid-basis
   total (same payments, different bucketing).
3. For non-catering payments `paid_business_date = business_date` in ≥ ~99% of
   rows (spot-check) — divergence concentrated in Catering/Invoicing dining
   options.
4. The denied/voided warning caption appears whenever Denied or Voided is
   selected, with the correct excluded-money figure.

## 4. Out of scope

Other tabs stay on event basis (revenue attribution is event-date by project
policy). No changes to channel attribution, `pc_*`, or fact tables.
