# Plan — `fees` column + dedicated `order_refunds` table

**Repos:** PMIX-Pipeline (Part A) + PMIX-Dashboard (Part B), done back-to-back so the
dashboard is never broken by the schema change.

## 0. Ground truth this plan is built on

1. **`public.br_order_payment.refund_amount`** today comes from `checks[].payments[].refund.refundAmount`
   in the raw Toast payload (`parse/orders.py:61`). It's dated implicitly by whatever
   `business_date`/`paid_business_date` the *payment* has — never by when the refund
   itself happened.
2. **The raw payload actually has more refund detail we don't capture at all**, on that
   same `payment.refund` object:
   ```json
   "refund": {
     "refundDate": "2026-02-01T22:28:15.795+0000",
     "refundAmount": 28.36,
     "tipRefundAmount": 4.01,
     "refundTransaction": { "guid": "7cc6930e-...", "entityType": "RefundTransaction" },
     "refundBusinessDate": 20260201
   }
   ```
   `refundBusinessDate` is the day the refund itself happened (int `YYYYMMDD`, same
   encoding as `businessDate`/`paidBusinessDate`) — this is the field that lets refunds
   be reported by their own date instead of smeared onto the original payment's date.
   `refundTransaction.guid` is a stable per-refund identifier, distinct from `payment_guid`.
3. **`originalProcessingFee`** also sits unused on every payment object (e.g. `0.93`,
   `0.85` in sampled payloads) — this is the new `fees` column's source.
4. **This is a different refund concept from `analytics.refund_sales`** (a raw-payload
   VIEW, not a pipeline table, read from `checks[].selections[].refundDetails` — a
   *line-item*-level refund used by Item Mix's "Refunds"/"Net after Refunds" columns
   and Overview's Net Revenue headline, per `LLM_HANDOVER_REFUNDS.md`). That system is
   untouched by this plan — different raw field, different join key (`selection_guid`
   vs `payment_guid`), different reporting surface. Also untouched: `fact_adjustments`'
   `kind='REFUND'` rows — that doc explicitly documents it as "a payment-level audit
   record, not the reporting source; it bundles tax differently," and confirmed nothing
   in the dashboard reads `fact_adjustments` at all today (checked — zero references in
   `lib/queries.ts`).
5. **Every current reader of `br_order_payment.refund_amount`** (dashboard side, all in
   `lib/queries.ts`): `getPayments`, `getPaymentsByLocation`, `getPaymentSourcesByLocation`
   — each does `ROUND(COALESCE(SUM(refund_amount), 0)::NUMERIC, 2) AS refunded_amount`.
   Consumed by `PaymentSource.tsx`'s "Refunded" KPI and the sources table's Refunds column.

## 1. Decisions locked in (confirmed with owner)

- **Refund date basis**: once split out, refund reporting filters by `refund_date`
  (when the refund happened), not by the original payment's `business_date`/
  `paid_business_date`. A payment made Jul 5 but refunded Jul 8 shows as an Jul 8 refund.
- **`order_refunds` columns**: IDs + `refund_date` + `refund_amount` + `tip_refund_amount`
  (capturing everything Toast gives us about the refund, not just the bare minimum).
- **Sequencing**: pipeline (Part A) and dashboard (Part B) done together, no window
  where the dashboard queries a dropped column.

## 2. Schema changes (Part A — PMIX-Pipeline)

All additive first; the actual `DROP COLUMN` is the last step, after the new table is
populated and verified (see §5).

### 2.1 New migration `sql/017_fees_and_refunds.sql` (idempotent)

```sql
-- fees: originalProcessingFee, previously unused in the raw payload.
ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS fees NUMERIC;
ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS fees NUMERIC;

-- order_refunds: dedicated table for payment-level refunds, keyed on Toast's own
-- refund_transaction_guid (not payment_guid — a payment's refund object is really
-- its own event with its own identity, and this matches the payment_guid-as-PK
-- convention already used for br_order_payment itself).
CREATE TABLE IF NOT EXISTS staging.order_refunds (
  refund_transaction_guid TEXT,
  payment_guid            TEXT,
  order_guid              TEXT,
  check_guid              TEXT,
  location_code           TEXT,
  refund_date             INT,      -- YYYYMMDD, same encoding as business_date
  refund_amount           NUMERIC,
  tip_refund_amount       NUMERIC
);

CREATE TABLE IF NOT EXISTS public.order_refunds (
  refund_transaction_guid TEXT PRIMARY KEY,
  payment_guid            TEXT NOT NULL,
  order_guid              TEXT NOT NULL,
  check_guid              TEXT NOT NULL,
  location_code           TEXT NOT NULL,
  refund_date             DATE NOT NULL,
  refund_amount           NUMERIC,
  tip_refund_amount       NUMERIC
);
CREATE INDEX IF NOT EXISTS idx_order_refunds_payment  ON public.order_refunds (payment_guid);
CREATE INDEX IF NOT EXISTS idx_order_refunds_date     ON public.order_refunds (refund_date);
```

`db.py::merge_to_public()` gets this file added the same way `015`/`016` are (with the
same `_column_exists`-style guard so it only runs once, not every merge — same fix as
the DDL-lock issue from the payment-basis-toggle work).

### 2.2 `parse/orders.py`

- `ParsedOrder` dataclass: add `order_refunds: list[dict] = field(default_factory=list)`.
- Payments block: add `"fees": pay.get("originalProcessingFee")`, **remove**
  `"refund_amount": ...` (moves to the new list below).
- New extraction, alongside the existing per-payment loop:
  ```python
  refund = pay.get("refund")
  if refund:
      out.order_refunds.append({
          "refund_transaction_guid": (refund.get("refundTransaction") or {}).get("guid", ""),
          "payment_guid":  pay.get("guid", ""),
          "order_guid":    order_guid,
          "check_guid":    check_guid,
          "location_code": location_code,
          "refund_date":       refund.get("refundBusinessDate"),
          "refund_amount":     refund.get("refundAmount"),
          "tip_refund_amount": refund.get("tipRefundAmount"),
      })
  ```
  The existing `fact_adjustments` REFUND-kind append (lines 64–71) stays exactly as-is —
  per §0.4, that's a separate, intentionally-kept audit trail, not something this plan
  touches.

### 2.3 `db.py`

- `_COPY_SPECS["payments"]`: add `"fees"`, remove `"refund_amount"`.
- New `_COPY_SPECS["order_refunds"]`, columns in the order above (`refund_transaction_guid`
  first, so `bulk_stage`'s generic `keyc = cols[0]` dedup path works with no special-case
  code — same mechanism `payment_guid`/`check_guid` already use, no changes needed to
  `bulk_stage()` itself).
- `cmd_reparse` (cli.py): add a `db.bulk_stage(conn, "order_refunds", parsed.order_refunds)`
  call alongside the existing calls for payments/checks/modifiers/etc.

### 2.4 `sql/005_merge_to_public.sql`

- `br_order_payment` INSERT: add `fees` to the column list/SELECT/`DO UPDATE SET`;
  **remove** `refund_amount` from all three (deferred to §5, see below — this specific
  edit only lands once the drop actually happens, not in the same commit as the new
  table, so nothing is ever mid-migration-broken).
- New INSERT block for `public.order_refunds`, same shape as the existing
  `br_order_payment` one: `to_date(refund_date::text,'YYYYMMDD')` conversion,
  `ON CONFLICT (refund_transaction_guid) DO UPDATE SET ...`.

## 3. Dashboard changes (Part B — PMIX-Dashboard)

### 3.1 `lib/queries.ts` — `getPayments`, `getPaymentsByLocation`, `getPaymentSourcesByLocation`

Replace the inline `SUM(refund_amount)` with a `LEFT JOIN` to a pre-aggregated
`order_refunds` subquery, filtered by **`refund_date`** independent of the `basis`
param (basis is a payment-date concept; refund_date is its own dimension per §1):

```sql
LEFT JOIN (
  SELECT payment_guid, SUM(refund_amount) AS refund_amount
  FROM public.order_refunds
  WHERE refund_date BETWEEN $1::DATE AND $2::DATE
  GROUP BY payment_guid
) orf ON orf.payment_guid = p.payment_guid   -- or bare payment_guid in getPayments, no alias there today
```
`refunded_amount` becomes `ROUND(COALESCE(SUM(orf.refund_amount), 0)::NUMERIC, 2)`.
Same `$1`/`$2` params as the existing date range — just a different column being
filtered underneath, on a joined table instead of the row itself.

This means: a payment made Jul 3 but refunded Jul 8 will show `$0` refunded when
viewing Jul 1–5, and the refund amount when viewing Jul 6–10 — matching the new
refund_date-based semantics from §1, regardless of which basis toggle is selected.

### 3.2 Types / UI

- `lib/types.ts`: `PaymentRow`/`PaymentByLocationRow`/`PaymentSourceLocationRow` gain
  a `fees` field (`ROUND(COALESCE(SUM(fees),0)::NUMERIC,2)`, same three query sites as
  `refunded_amount`) alongside the existing `refunded_amount`.
- `PaymentSource.tsx`: no changes needed for `refunded_amount`'s new source (still just
  consumes the field) — but see §3.3 for the three new KPI cards, which do need new
  aggregation logic in this file.
- Caption update: the "Refunded" KPI's existing subtext ("not netted out of Total
  Payments above") stays, plus consider adding a short note that Refunded reflects
  refunds *processed* in this window, not refunds *of* payments made in this window —
  worth a one-line caption given this is a real behavior change from today.

### 3.3 Three new KPI cards on the Payment Source tab

Alongside the existing five (Total Payments, Refunded, Card, Alt Payments, Avg Ticket):

| Card | Formula | Notes |
|---|---|---|
| **Amount + Tip** | `SUM(amount) + SUM(tip_amount)` | Gross actually charged to the card, tip included — `amount`/`tip_amount` already exist on every row, no new data needed. |
| **Fees** | `SUM(fees)` | The new column from §2, same date/basis/status filter as everything else on the tab. |
| **Net (Amt+Tip−Fees−Refunds)** | `(SUM(amount)+SUM(tip_amount)) − SUM(fees) − SUM(refunded_amount)` | Closest we can get, from our own data, to Toast's Payout-report "Payout total" concept from the earlier investigation — not identical (still no true settlement-date grouping, per that investigation's conclusion), but a real net-cash-impact figure. |

Implementation: `PaymentRow` (and the by-location/by-source variants) gains `fees`;
`PaymentSource.tsx` adds `totalTip`, `totalFees` reductions alongside the existing
`totalRevenue`/`totalRefunded`, and a `netAfterFeesAndRefunds` derived value, each
rendered as its own `.kc` card in the KPI row. Grid currently `repeat(5,1fr)` for 5
cards — becomes 8 cards, needs a layout revisit (e.g. `repeat(4,1fr)` over two rows,
or wrap via `auto-fit`/`minmax`) rather than force-fitting 8 into one row.

## 4. Backfill

Same mechanism as the `paid_business_date` rollout: `reparse --start <X> --end <today> --merge`.
**Open question — what should `<X>` be?** The last backfill used `2025-12-19` (matching
that spec's needs). `fees` and refund_date/tip_refund_amount have never been captured at
all, so a narrower start date means older payments simply have `fees IS NULL` / no
`order_refunds` row forever (not wrong, just incomplete). Options:
1. Reuse `2025-12-19` — consistent with existing backfilled range, fastest.
2. Go back to the true earliest available raw data (`dbMin`) — full historical accuracy,
   but a much bigger reparse (unknown duration until checked).

**Recommend option 1** unless historical fees/refund-by-date reporting further back than
Dec 19 is actually needed — confirm before running.

## 5. Rollout sequence — each step verified before the next

1. **Add `fees` + create both `order_refunds` tables** (§2.1) — purely additive, no
   lock risk beyond the one-time DDL (same guarded pattern as 015/016, so it only
   costs a lock once, on the very first merge after this ships).
2. **Ship parser + db.py + 005 changes for `fees` and the new INSERT into
   `order_refunds`** — `br_order_payment.refund_amount` keeps being populated
   exactly as today in parallel (not yet touched) — so nothing downstream breaks yet.
3. **Backfill** via reparse+merge (§4) → verify: `SUM(order_refunds.refund_amount)`
   for a known window matches `SUM(br_order_payment.refund_amount)` for the same
   window (should match closely; any gap is refunds whose *payment* falls outside the
   window but whose *refund* falls inside, or vice versa — expected given the date-basis
   change, not a bug).
4. **Ship the three dashboard query changes** (§3.1) reading from `order_refunds`
   instead of the column → verify: "Refunded" KPI for Jul 6–12 recomputes and is
   within expected tolerance of the pre-change number (won't be byte-identical, by
   design — different date basis).
5. **Only now**, drop `refund_amount` from `staging.payments` and
   `public.br_order_payment`, and remove it from the `005` INSERT/COPY specs —
   in the same deploy as step 4, so there's never a window where the dashboard
   queries a dropped column.

## 6. Verification checklist

- [ ] `fees` populated for a sample of recent payments, spot-checked against raw
      `originalProcessingFee`.
- [ ] `order_refunds` row count for a known week matches the count of non-null
      `refund` objects in raw payloads for that week (spot SQL against `raw.toast_orders`).
- [ ] `SUM(refund_amount)` parity check from step 5.3 above.
- [ ] Dashboard "Refunded" KPI shows a sensible, explainable number post-cutover
      (expected to differ slightly from before, per the date-basis change — confirm
      the *direction* of the difference makes sense, e.g. late-window refunds of
      early-window payments now excluded).
- [ ] `tsc --noEmit`, lint, build clean on the dashboard side.
- [ ] No lock incident on the merge that ships the schema change (watch
      `pg_stat_activity` during the first post-deploy merge, same as the
      `paid_business_date` rollout).

## 7. Out of scope

- `analytics.refund_sales` / Item Mix "Refunds" / "Net after Refunds" / Overview Net
  Revenue — untouched, different system entirely (§0.4).
- `fact_adjustments` — untouched, stays as the payment-level audit trail it already is.
- Toast's Payout/settlement-date reporting (from the earlier investigation) — still not
  reproducible; this plan doesn't add that data, only fees and payment-level refunds.
