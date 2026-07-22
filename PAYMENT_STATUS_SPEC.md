# Spec — Payment status & refunds on the Payment Source tab

*Prepared 2026-07-22 from production data + the owner's Toast exports
(`attachment db check for dashboard P5 (1).xlsx`). The Toast↔Neon reconciliation
is CLOSED — both P5 and the Jul 6–12 week tie to the cent (see §1). What remains
are two real correctness fixes: denied/voided payments counted as collected money,
and refunds invisible on the tab.*

**For:** Anushka · **Repos:** PMIX-Pipeline (part A) + PMIX-Dashboard (part B)
**From:** Rishabh + Claude · exact change + live expected numbers + validation SQL.

---

## 1. Reconciliation result (context — no action needed)

Toast's payments report lists payments by **paid date**; our DB attributes by
**business date** (catering = event date, the agreed policy). Aligning basis makes
both windows tie exactly:

- **P5:** Toast $628,851.12 − $11,170.89 (18 payments taken in P5 for post-P5
  events) + $7,612.71 (11 payments taken before P5 for in-P5 events)
  = **$625,292.94 = Neon, to the cent.**
- **Week Jul 6–12:** Toast $178,407.51 − $3,954.70 (6 in-week payments for Jul
  13–18 events) + $2,905.57 (3 payments paid Jul 1–2 for in-week events)
  = **$177,358.38 = Neon, to the cent.** (Residual rows verified individually —
  all Catering - Delivery.)

Dashboard-vs-Neon deltas are cache staleness only (same query, older snapshot).

## 2. The two real bugs

1. **DENIED and VOIDED payments count as revenue.** The parser lands every
   payment with no status filter, and `br_order_payment` doesn't store the
   status. Week Jul 6–12: 19 DENIED / $329.88 + 15 VOIDED / $502.31 = **34
   payments / $832.19** of money never collected, in every KPI on the tab. P5
   contains ≥44 voided / $1,666.70 (per Toast's own export).
2. **Refunds are invisible on this tab** (≥ $1,020.46 in the week). Refund data
   exists in the payload per payment (`refund.refundAmount`) but isn't stored on
   the payment row.

Latent third bug fixed in passing: the merge uses
`ON CONFLICT (payment_guid) DO NOTHING` (005_merge_to_public.sql:110) — a payment
that is voided or refunded *after* first ingest **never updates** in public.
Switching to DO UPDATE is required for the new columns to backfill on reparse
anyway, and fixes this.

## 3. Part A — pipeline (PMIX-Pipeline)

Source field: `paymentStatus` on each check payment in the raw payload
(values seen: `CAPTURED`, `AUTHORIZED`, `DENIED`, `VOIDED` — matches Toast's
export exactly). Refund: `refund.refundAmount` on the same object.

1. **New migration `015_payment_status.sql`** (idempotent):
   ```sql
   ALTER TABLE staging.payments        ADD COLUMN IF NOT EXISTS paid_status TEXT,
                                       ADD COLUMN IF NOT EXISTS refund_amount NUMERIC;
   ALTER TABLE public.br_order_payment ADD COLUMN IF NOT EXISTS paid_status TEXT,
                                       ADD COLUMN IF NOT EXISTS refund_amount NUMERIC;
   ```
2. **`parse/orders.py`** (payments block, ~line 50):
   ```python
   "paid_status":   pay.get("paymentStatus"),
   "refund_amount": (pay.get("refund") or {}).get("refundAmount"),
   ```
3. **`db.py`**: add the two columns to the staging COPY column list for
   `staging.payments`.
4. **`005_merge_to_public.sql`**: add both columns to the INSERT and replace
   `ON CONFLICT (payment_guid) DO NOTHING` with
   ```sql
   ON CONFLICT (payment_guid) DO UPDATE SET
       payment_type     = EXCLUDED.payment_type,
       alt_payment_name = EXCLUDED.alt_payment_name,
       amount           = EXCLUDED.amount,
       tip_amount       = EXCLUDED.tip_amount,
       paid_status      = EXCLUDED.paid_status,
       refund_amount    = EXCLUDED.refund_amount;
   ```
   (freshest-payload-wins, same doctrine as checks/lines).
5. **Backfill**: `reparse --start 2025-12-19 --end <today> --merge` (full window —
   payments are cheap; one pass backfills every row).

## 4. Part B — dashboard (PMIX-Dashboard)

1. **`getPayments`** (lib/queries.ts:1893) and **`getPaymentsByLocation`**
   (:1924): add to both WHERE clauses (and the `grand` CTE):
   ```sql
   AND COALESCE(paid_status, 'CAPTURED') NOT IN ('DENIED', 'VOIDED')
   ```
   The COALESCE keeps the tab correct even before the backfill completes.
2. **Refunds KPI**: add a small "Refunded" stat to the Payment Source tab —
   `SUM(refund_amount)` over the same range/filters (mirror the Overview tab's
   Refunds KPI treatment; show it as its own number, do NOT silently net it out
   of the payment totals).
3. Optional/cosmetic (owner hasn't ruled): `GIFTCARD` currently lands under
   "Alt Payment" because only `CREDIT` maps to Card. Fine to leave; if touched,
   give gift cards their own label rather than reclassifying as Card.

Out of scope: the channel-attribution LATERAL on `br_order_payment` (picks the
max-amount alt payment per order) — a denied alt payment could in theory
mis-channel an order, but no observed case; flagged for a later pass.

## 5. Acceptance (live numbers, captured 2026-07-22)

1. **Post-backfill status distribution, week Jul 6–12** (must match Toast's
   export exactly):
   ```sql
   SELECT paid_status, COUNT(*), SUM(amount)::NUMERIC(12,2)
   FROM public.br_order_payment
   WHERE business_date BETWEEN '2026-07-06' AND '2026-07-12'
   GROUP BY 1;
   -- CAPTURED   4813  176488.51
   -- AUTHORIZED    2      37.68
   -- DENIED       19     329.88
   -- VOIDED       15     502.31
   ```
   No NULL `paid_status` rows anywhere after the full reparse.
2. **Payment tab, week Jul 6–12 after the filter**: 4,815 payments /
   **$176,526.19** (was 4,849 / $177,358.38).
3. **P5 (Apr 27 – May 24)**: `paid_status = 'VOIDED'` rows ≈ 44 / ≈ $1,666.70
   (Toast's export shows exactly that; small drift from late corrections OK).
4. **Refunds**: week `SUM(refund_amount)` ≥ $1,020.46 (the two refunded payments
   in Toast's week export; more may exist on payments Toast's paid-date window
   excluded).
5. **Invariance**: `fact_order_lines` / revenue totals completely untouched;
   `COUNT(*)` of `br_order_payment` unchanged (no rows added/removed — only the
   two new columns populated and stale rows refreshed).
6. Reparse re-run → zero changes (idempotence).

## 6. Notes

- The payload also carries `paidBusinessDate` per payment — if RASA ever wants a
  "paid basis" payments view (matching Toast's report exactly, prepayments and
  all), it's one column away. Not in scope.
- When anyone compares the tab to Toast's payment report again: filter Toast to
  CAPTURED+AUTHORIZED and remember the paid-date vs event-date edges (§1) — the
  numbers then tie to the cent.
