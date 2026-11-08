"""The matching engine: what each order should have paid, what it did, who owes the gap (TDD §8).

Everything is set-based SQL over temp tables, so re-matching 1 lakh orders is a handful of
statements, not 1 lakh round trips. A run covers only the orders in its scope:

    recon_todo    the orders to re-match
    recon_calc    per order: expected and actual per component, gaps, issues and status
    order_recon   the stored result, replaced for those orders
    small_gaps    gaps within tolerance, kept per component for the pattern check
    recon_issues  the claims these orders should have; live claims are brought in line with it

Signs: positive is money to Company A. Difference = received - expected. TCS and TDS never enter
expected, received or any gap; they are reported separately.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from app.enums import FileKind

LOCAL_TZ = ZoneInfo("Asia/Kolkata")
SETTLED_BELOW_PAISE = 100  # gaps under ₹1 are rounding [A13]
# Nobody can be called late until their reports reach the due date; the backstops catch a partner
# or warehouse that has simply stopped sending reports [A25].
PAYMENT_BACKSTOP_DAYS = 30
RETURN_BACKSTOP_DAYS = 7
PATTERN_SUM_PAISE = 500_000  # ₹5,000 one way per partner, component and month [A15]
PATTERN_MIN_ORDERS = 50
PATTERN_ONE_WAY_SHARE = 0.8

_LIVE = "('open', 'raised', 'partner_rejected')"


def today() -> date:
    return datetime.now(LOCAL_TZ).date()


# Small lookups and per-order actuals are temp tables with fresh statistics, so the join below is
# planned on real row counts. As one big statement with CTEs the planner guessed badly: 21 s for
# 60k orders, and 20 minutes with the CTEs materialized.
_ACTUAL = """
CREATE TEMP TABLE recon_actual ON COMMIT DROP AS
SELECT pl.order_id,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'sale'), 0) AS a_sale,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'commission'), 0) AS a_commission,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'fixed_fee'), 0) AS a_fixed,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'shipping_fee'), 0) AS a_shipping,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'return_shipping_fee'), 0)
        AS a_return_shipping,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'adjustment'), 0) AS a_adjustment,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'freight_forward'), 0) AS a_forward,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'freight_return'), 0) AS a_return,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'cod_fee'), 0) AS a_cod,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'tcs'), 0) AS a_tcs,
    coalesce(sum(amount_paise) FILTER (WHERE component = 'tds'), 0) AS a_tds,
    bool_or(component IN ('sale', 'commission', 'fixed_fee', 'shipping_fee',
                          'return_shipping_fee', 'adjustment')) AS has_pay,
    bool_or(component = 'freight_forward') AS has_forward,
    bool_or(component = 'freight_return') AS has_return,
    bool_or(component = 'cod_fee') AS has_cod,
    max(txn_date) FILTER (WHERE component NOT IN ('freight_forward', 'freight_return',
                                                  'cod_fee', 'tcs', 'tds')) AS pay_date,
    max(txn_date) FILTER (WHERE component IN ('freight_forward', 'freight_return',
                                              'cod_fee')) AS charge_date,
    max(zone) AS line_zone
FROM partner_lines pl
JOIN recon_todo t ON t.order_id = pl.order_id
GROUP BY pl.order_id
"""

# A rate card runs until the next one starts, so versions can't overlap.
_CARDS = """
CREATE TEMP TABLE recon_cards ON COMMIT DROP AS
SELECT id, partner_id, rules, effective_from,
    least(coalesce(effective_to, 'infinity'::date),
          coalesce(lead(effective_from) OVER (PARTITION BY partner_id
                                              ORDER BY effective_from, version) - 1,
                   'infinity'::date)) AS effective_to
FROM rate_cards
"""

_CHANNEL_CARDS = """
CREATE TEMP TABLE recon_channel_cards ON COMMIT DROP AS
SELECT id, partner_id, effective_from, effective_to,
    (rules->>'commission_pct')::numeric AS commission_pct,
    rules->>'commission_rounding' = 'rupee' AS commission_in_rupees,
    (rules->>'fixed_fee')::numeric * 100 AS fixed_fee,
    (rules#>>'{shipping,slab_g}')::numeric AS ship_slab,
    (rules#>>'{shipping,first}')::numeric * 100 AS ship_first,
    (rules#>>'{shipping,additional}')::numeric * 100 AS ship_additional,
    coalesce((rules#>>'{on_return,refund_commission}')::boolean, true) AS refund_commission,
    coalesce((rules#>>'{on_return,refund_fixed_fee}')::boolean, true) AS refund_fixed,
    coalesce((rules#>>'{on_return,return_shipping_pct_of_forward}')::numeric, 100)
        AS return_ship_pct
FROM recon_cards WHERE rules->>'type' = 'marketplace'
"""

_COURIER_CARDS = """
CREATE TEMP TABLE recon_courier_cards ON COMMIT DROP AS
SELECT id, partner_id, effective_from, effective_to,
    (rules->>'slab_g')::numeric AS slab,
    (rules->>'return_pct_of_forward')::numeric AS return_pct,
    (rules#>>'{cod_fee,pct}')::numeric AS cod_pct,
    (rules#>>'{cod_fee,min}')::numeric * 100 AS cod_min
FROM recon_cards WHERE rules->>'type' = 'courier'
"""

_ZONES = """
CREATE TEMP TABLE recon_zones ON COMMIT DROP AS
SELECT c.id AS card_id, z.key AS zone,
    (z.value->>'first')::numeric * 100 AS zone_first,
    (z.value->>'additional')::numeric * 100 AS zone_additional
FROM recon_cards c, jsonb_each(c.rules->'forward') z
WHERE c.rules->>'type' = 'courier'
"""

_PAID_THROUGH = """
CREATE TEMP TABLE recon_paid_through ON COMMIT DROP AS
SELECT partner_id, max(txn_date) AS through
FROM partner_lines WHERE component = 'sale' GROUP BY partner_id
"""

_TEMP_TABLES = (
    "recon_calc",
    "recon_issues",
    "recon_patterns",
    "recon_actual",
    "recon_cards",
    "recon_channel_cards",
    "recon_courier_cards",
    "recon_zones",
    "recon_paid_through",
    "recon_todo",
)

_CALC = """
CREATE TEMP TABLE recon_calc ON COMMIT DROP AS
WITH base AS (
    SELECT o.id AS order_id, o.value_paise, o.payment_mode, o.delivery_status,
        o.delivered_at, o.returned_at, o.declared_weight_g AS weight,
        o.courier_partner_id,
        ch.kind IN ('marketplace', 'quick_commerce') AS settled_by_channel,
        o.delivery_status = 'cancelled' AS cancelled,
        o.delivery_status IN ('return_in_transit', 'returned_to_warehouse') AS is_return,
        CASE WHEN ch.kind IN ('marketplace', 'quick_commerce') THEN o.channel_partner_id
             ELSE o.courier_partner_id END AS payer_id,
        ch.settlement_cycle_days AS channel_cycle, co.settlement_cycle_days AS courier_cycle,
        ch.tolerance_paise AS channel_tolerance,
        coalesce(co.tolerance_paise, 1000) AS courier_tolerance,
        CASE WHEN ch.kind IN ('marketplace', 'quick_commerce') THEN ch.return_window_days
             ELSE coalesce(co.return_window_days, ch.return_window_days) END AS return_window,
        coalesce(a.a_sale, 0) AS a_sale, coalesce(a.a_commission, 0) AS a_commission,
        coalesce(a.a_fixed, 0) AS a_fixed, coalesce(a.a_shipping, 0) AS a_shipping,
        coalesce(a.a_return_shipping, 0) AS a_return_shipping,
        coalesce(a.a_adjustment, 0) AS a_adjustment, coalesce(a.a_forward, 0) AS a_forward,
        coalesce(a.a_return, 0) AS a_return, coalesce(a.a_cod, 0) AS a_cod,
        coalesce(a.a_tcs, 0) AS a_tcs, coalesce(a.a_tds, 0) AS a_tds,
        coalesce(a.has_pay, false) AS has_pay, coalesce(a.has_forward, false) AS has_forward,
        coalesce(a.has_return, false) AS has_return, coalesce(a.has_cod, false) AS has_cod,
        a.order_id IS NOT NULL AS has_lines, a.pay_date, a.charge_date,
        EXISTS (SELECT 1 FROM return_scans rs WHERE rs.order_id = o.id) AS has_scan,
        pt.through AS payer_through, CAST(:scans_through AS date) AS scans_through,
        cc.id AS channel_card_id, kc.id AS courier_card_id,
        cc.commission_pct, cc.commission_in_rupees, cc.fixed_fee, cc.ship_slab, cc.ship_first,
        cc.ship_additional, cc.refund_commission, cc.refund_fixed, cc.return_ship_pct,
        kc.slab, kc.return_pct, kc.cod_pct, kc.cod_min, z.zone_first, z.zone_additional
    FROM recon_todo t
    JOIN orders o ON o.id = t.order_id
    JOIN partners ch ON ch.id = o.channel_partner_id
    LEFT JOIN partners co ON co.id = o.courier_partner_id
    LEFT JOIN recon_actual a ON a.order_id = o.id
    LEFT JOIN recon_channel_cards cc ON cc.partner_id = o.channel_partner_id
        AND o.order_date BETWEEN cc.effective_from AND cc.effective_to
    LEFT JOIN recon_courier_cards kc ON kc.partner_id = o.courier_partner_id
        AND o.order_date BETWEEN kc.effective_from AND kc.effective_to
    LEFT JOIN recon_zones z ON z.card_id = kc.id AND z.zone = coalesce(o.zone, a.line_zone)
    LEFT JOIN recon_paid_through pt ON pt.partner_id =
        CASE WHEN ch.kind IN ('marketplace', 'quick_commerce') THEN o.channel_partner_id
             ELSE o.courier_partner_id END
),
rates AS MATERIALIZED (
    SELECT b.*,
        CASE WHEN commission_in_rupees
             THEN round(round(value_paise * commission_pct / 100) / 100) * 100
             ELSE round(value_paise * commission_pct / 100) END AS commission,
        -- No shipping block on the card means the partner charges no shipping.
        CASE WHEN channel_card_id IS NULL THEN NULL
             WHEN ship_slab IS NULL THEN 0
             ELSE ship_first
                  + (greatest(1, ceil(weight::numeric / ship_slab)) - 1) * ship_additional
             END AS ship,
        zone_first + (greatest(1, ceil(weight::numeric / slab)) - 1) * zone_additional AS freight,
        greatest(cod_min, round(value_paise * cod_pct / 100)) AS cod
    FROM base b
),
-- Expected per component (TDD §8.2). A missing rate leaves it null: nothing to compare against.
expected AS (
    SELECT r.*,
        CASE WHEN cancelled THEN 0
             WHEN settled_by_channel THEN CASE WHEN is_return THEN 0 ELSE value_paise END
             WHEN payment_mode = 'cod' AND NOT is_return THEN value_paise
             ELSE 0 END AS raw_sale,
        CASE WHEN cancelled OR NOT settled_by_channel THEN 0
             WHEN is_return AND refund_commission THEN 0
             ELSE -commission END AS raw_commission,
        CASE WHEN cancelled OR NOT settled_by_channel THEN 0
             WHEN is_return AND refund_fixed THEN 0
             ELSE -fixed_fee END AS raw_fixed,
        CASE WHEN cancelled OR NOT settled_by_channel THEN 0 ELSE -ship END AS raw_shipping,
        CASE WHEN cancelled OR NOT settled_by_channel OR NOT is_return THEN 0
             ELSE -round(ship * return_ship_pct / 100) END AS raw_return_shipping,
        CASE WHEN cancelled OR settled_by_channel THEN 0 ELSE -freight END AS raw_forward,
        CASE WHEN cancelled OR settled_by_channel OR NOT is_return THEN 0
             ELSE -round(freight * return_pct / 100) END AS raw_return,
        CASE WHEN cancelled OR settled_by_channel OR is_return OR payment_mode <> 'cod' THEN 0
             ELSE -cod END AS raw_cod
    FROM rates r
),
amounts AS MATERIALIZED (
    SELECT e.*,
        coalesce(raw_sale, a_sale)::bigint AS e_sale,
        coalesce(raw_commission, a_commission)::bigint AS e_commission,
        coalesce(raw_fixed, a_fixed)::bigint AS e_fixed,
        coalesce(raw_shipping, a_shipping)::bigint AS e_shipping,
        coalesce(raw_return_shipping, a_return_shipping)::bigint AS e_return_shipping,
        coalesce(raw_forward, a_forward)::bigint AS e_forward,
        coalesce(raw_return, a_return)::bigint AS e_return,
        coalesce(raw_cod, a_cod)::bigint AS e_cod,
        a_sale + a_commission + a_fixed + a_shipping + a_return_shipping + a_adjustment AS a_pay,
        a_forward + a_return + a_cod AS a_charges,
        CASE WHEN settled_by_channel THEN channel_tolerance
             ELSE courier_tolerance END AS pay_tolerance,
        -- When money is due: delivery (or the completed return) plus the partner's cycle [A12].
        CASE WHEN settled_by_channel AND delivery_status = 'delivered'
                 THEN delivered_at + channel_cycle
             WHEN settled_by_channel AND delivery_status = 'returned_to_warehouse'
                 THEN returned_at + channel_cycle
             WHEN NOT settled_by_channel AND payment_mode = 'cod'
                  AND delivery_status = 'delivered'
                 THEN delivered_at + courier_cycle
             END AS due_date,
        delivery_status = 'delivered' AND (settled_by_channel OR payment_mode = 'cod')
            AS expects_payment,
        returned_at + return_window AS return_due
    FROM expected e
),
gaps AS MATERIALIZED (
    SELECT a.*,
        e_sale + e_commission + e_fixed + e_shipping + e_return_shipping AS e_pay,
        e_forward + e_return + e_cod AS e_charges,
        CASE WHEN has_pay
             THEN a_pay - (e_sale + e_commission + e_fixed + e_shipping + e_return_shipping)
             ELSE 0 END AS pay_gap,
        -- Only charges already billed can be compared; an unbilled one is just not billed yet.
        CASE WHEN has_forward THEN a_forward - e_forward ELSE 0 END
            + CASE WHEN has_return THEN a_return - e_return ELSE 0 END
            + CASE WHEN has_cod THEN a_cod - e_cod ELSE 0 END AS charge_gap
    FROM amounts a
),
flags AS (
    SELECT g.*,
        delivery_status = 'returned_to_warehouse' AND NOT has_scan
            AND CAST(:as_of AS date) > return_due
            AND (return_due <= scans_through
                 OR (scans_through IS NOT NULL
                     AND CAST(:as_of AS date) > return_due + :return_backstop)) AS lost,
        expects_payment AND NOT has_pay AND e_pay + e_charges > 0
            AND CAST(:as_of AS date) > due_date
            AND (due_date <= payer_through
                 OR (payer_through IS NOT NULL
                     AND CAST(:as_of AS date) > due_date + :payment_backstop)) AS unpaid,
        has_pay AND pay_gap < -pay_tolerance AS short,
        charge_gap < -courier_tolerance AS overcharged,
        (has_pay AND pay_gap > pay_tolerance) OR charge_gap > courier_tolerance AS overpaid,
        delivery_status IN ('pending', 'in_transit', 'return_in_transit')
            OR (expects_payment AND NOT has_pay)
            OR (NOT expects_payment AND NOT has_pay AND e_pay <> 0)
            OR (delivery_status = 'returned_to_warehouse' AND NOT has_scan)
            OR (e_forward <> 0 AND NOT has_forward)
            OR (e_return <> 0 AND NOT has_return)
            OR (e_cod <> 0 AND NOT has_cod) AS waiting
    FROM gaps g
)
SELECT f.*,
    CASE WHEN lost THEN 'return_not_received'
         WHEN unpaid THEN 'unpaid_past_due'
         WHEN short THEN 'short_paid'
         WHEN overcharged THEN 'freight_overcharged'
         WHEN overpaid THEN 'overpaid'
         WHEN cancelled AND NOT has_lines THEN 'not_applicable'
         WHEN waiting THEN 'pending'
         WHEN abs(pay_gap) >= :settled_below OR abs(charge_gap) >= :settled_below
             THEN 'within_tolerance'
         ELSE 'settled' END AS status
FROM flags f
"""

_WRITE_RESULTS = """
INSERT INTO order_recon (order_id, expected_paise, received_paise, diff_paise, tcs_paise,
    tds_paise, status, issues, due_date, breakdown, channel_rate_card_id, courier_rate_card_id)
SELECT order_id,
    e_pay + e_charges,
    a_pay + a_charges,
    a_pay + a_charges - e_pay - e_charges,
    a_tcs,
    a_tds,
    status,
    array_remove(ARRAY[
        CASE WHEN lost THEN 'return_not_received' END,
        CASE WHEN unpaid THEN 'unpaid_past_due' END,
        CASE WHEN short THEN 'short_paid' END,
        CASE WHEN overcharged THEN 'freight_overcharged' END
    ], NULL),
    due_date,
    jsonb_strip_nulls(jsonb_build_object(
        'sale', CASE WHEN e_sale <> 0 OR a_sale <> 0 THEN jsonb_build_array(e_sale, a_sale) END,
        'commission', CASE WHEN e_commission <> 0 OR a_commission <> 0
                           THEN jsonb_build_array(e_commission, a_commission) END,
        'fixed_fee', CASE WHEN e_fixed <> 0 OR a_fixed <> 0
                          THEN jsonb_build_array(e_fixed, a_fixed) END,
        'shipping_fee', CASE WHEN e_shipping <> 0 OR a_shipping <> 0
                             THEN jsonb_build_array(e_shipping, a_shipping) END,
        'return_shipping_fee', CASE WHEN e_return_shipping <> 0 OR a_return_shipping <> 0
                                    THEN jsonb_build_array(e_return_shipping, a_return_shipping)
                                    END,
        'adjustment', CASE WHEN a_adjustment <> 0 THEN jsonb_build_array(0, a_adjustment) END,
        'freight_forward', CASE WHEN e_forward <> 0 OR a_forward <> 0
                                THEN jsonb_build_array(e_forward, a_forward) END,
        'freight_return', CASE WHEN e_return <> 0 OR a_return <> 0
                               THEN jsonb_build_array(e_return, a_return) END,
        'cod_fee', CASE WHEN e_cod <> 0 OR a_cod <> 0 THEN jsonb_build_array(e_cod, a_cod) END
    )),
    channel_card_id,
    courier_card_id
FROM recon_calc
"""

_WRITE_SMALL_GAPS = """
INSERT INTO small_gaps (order_id, component, partner_id, month, gap_paise)
SELECT c.order_id, g.component, g.partner_id, date_trunc('month', g.on_date)::date, g.gap
FROM recon_calc c
CROSS JOIN LATERAL (VALUES
    ('sale', c.a_sale - c.e_sale, c.has_pay, c.payer_id, c.pay_date),
    ('commission', c.a_commission - c.e_commission, c.has_pay, c.payer_id, c.pay_date),
    ('fixed_fee', c.a_fixed - c.e_fixed, c.has_pay, c.payer_id, c.pay_date),
    ('shipping_fee', c.a_shipping - c.e_shipping, c.has_pay, c.payer_id, c.pay_date),
    ('return_shipping_fee', c.a_return_shipping - c.e_return_shipping, c.has_pay, c.payer_id,
     c.pay_date),
    ('adjustment', c.a_adjustment, c.has_pay, c.payer_id, c.pay_date),
    ('freight_forward', c.a_forward - c.e_forward, c.has_forward, c.courier_partner_id,
     c.charge_date),
    ('freight_return', c.a_return - c.e_return, c.has_return, c.courier_partner_id, c.charge_date),
    ('cod_fee', c.a_cod - c.e_cod, c.has_cod, c.courier_partner_id, c.charge_date)
) AS g(component, gap, billed, partner_id, on_date)
WHERE c.status = 'within_tolerance' AND g.billed AND g.gap <> 0 AND g.partner_id IS NOT NULL
"""

_ISSUES = """
CREATE TEMP TABLE recon_issues ON COMMIT DROP AS
SELECT order_id, payer_id AS partner_id, 'return_not_received' AS type,
       value_paise AS amount_paise, return_due AS aging_from
FROM recon_calc WHERE lost AND payer_id IS NOT NULL
UNION ALL
SELECT order_id, payer_id, 'unpaid_past_due', e_pay + e_charges, due_date
FROM recon_calc WHERE unpaid AND payer_id IS NOT NULL
UNION ALL
SELECT order_id, payer_id, 'short_paid', -pay_gap, pay_date
FROM recon_calc WHERE short AND payer_id IS NOT NULL
UNION ALL
SELECT order_id, courier_partner_id, 'freight_overcharged', -charge_gap, charge_date
FROM recon_calc WHERE overcharged AND courier_partner_id IS NOT NULL
"""

_UPDATE_CLAIMS = f"""
UPDATE claims c
SET amount_paise = i.amount_paise, aging_from = i.aging_from, updated_at = now()
FROM recon_issues i
WHERE c.order_id = i.order_id AND c.type = i.type AND c.status IN {_LIVE}
  AND (c.amount_paise, c.aging_from) IS DISTINCT FROM (i.amount_paise, i.aging_from)
"""

# A claim already recovered or written off isn't opened again; one that closed itself can be.
_OPEN_CLAIMS = """
WITH created AS (
    INSERT INTO claims (partner_id, order_id, type, amount_paise, aging_from)
    SELECT i.partner_id, i.order_id, i.type, i.amount_paise, i.aging_from
    FROM recon_issues i
    WHERE NOT EXISTS (
        SELECT 1 FROM claims c
        WHERE c.order_id = i.order_id AND c.type = i.type AND c.status <> 'auto_resolved'
    )
    RETURNING id
)
INSERT INTO claim_events (claim_id, to_status, note)
SELECT id, 'open', 'Found by matching' FROM created
"""

_RESOLVE_CLAIMS = f"""
WITH gone AS (
    SELECT c.id, c.status
    FROM claims c
    JOIN recon_todo t ON t.order_id = c.order_id
    WHERE c.status IN {_LIVE}
      AND NOT EXISTS (
          SELECT 1 FROM recon_issues i WHERE i.order_id = c.order_id AND i.type = c.type
      )
),
closed AS (
    UPDATE claims c SET status = 'auto_resolved', closed_at = now(), updated_at = now()
    FROM gone WHERE c.id = gone.id
)
INSERT INTO claim_events (claim_id, from_status, to_status, note)
SELECT id, status, 'auto_resolved',
    'Matched again and the gap is gone: a later file paid it, or a rate card changed'
FROM gone
"""

_PATTERNS = """
CREATE TEMP TABLE recon_patterns ON COMMIT DROP AS
SELECT partner_id, component, month, -sum(gap_paise) AS amount_paise,
    least(CAST(:as_of AS date), (month + interval '1 month')::date - 1) AS aging_from,
    count(*) AS orders
FROM small_gaps
GROUP BY partner_id, component, month
HAVING sum(gap_paise) < 0 AND (
    sum(gap_paise) <= -:pattern_sum
    OR (count(*) >= :pattern_orders
        AND count(*) FILTER (WHERE gap_paise < 0) >= :pattern_share * count(*))
)
"""

_MATCH_PATTERN = """
c.type = 'systematic_gap' AND c.partner_id = p.partner_id AND c.component = p.component
AND c.period_month = p.month
"""

_UPDATE_PATTERN_CLAIMS = f"""
UPDATE claims c
SET amount_paise = p.amount_paise, aging_from = p.aging_from, updated_at = now()
FROM recon_patterns p
WHERE {_MATCH_PATTERN} AND c.status IN {_LIVE}
  AND (c.amount_paise, c.aging_from) IS DISTINCT FROM (p.amount_paise, p.aging_from)
"""

_OPEN_PATTERN_CLAIMS = f"""
WITH created AS (
    INSERT INTO claims (partner_id, type, amount_paise, component, period_month, aging_from)
    SELECT p.partner_id, 'systematic_gap', p.amount_paise, p.component, p.month, p.aging_from
    FROM recon_patterns p
    WHERE NOT EXISTS (
        SELECT 1 FROM claims c WHERE {_MATCH_PATTERN} AND c.status <> 'auto_resolved'
    )
    RETURNING id
)
INSERT INTO claim_events (claim_id, to_status, note)
SELECT id, 'open', 'Small gaps pointing one way' FROM created
"""

_RESOLVE_PATTERN_CLAIMS = f"""
WITH gone AS (
    SELECT c.id, c.status FROM claims c
    WHERE c.type = 'systematic_gap' AND c.status IN {_LIVE}
      AND NOT EXISTS (SELECT 1 FROM recon_patterns p WHERE {_MATCH_PATTERN})
),
closed AS (
    UPDATE claims c SET status = 'auto_resolved', closed_at = now(), updated_at = now()
    FROM gone WHERE c.id = gone.id
)
INSERT INTO claim_events (claim_id, from_status, to_status, note)
SELECT id, status, 'auto_resolved',
    'Matched again and the pattern is gone: a later file paid it, or a rate card changed'
FROM gone
"""

_LINK_PATTERN_ORDERS = f"""
INSERT INTO claim_orders (claim_id, order_id, amount_paise)
SELECT c.id, g.order_id, -g.gap_paise
FROM claims c
JOIN small_gaps g ON g.partner_id = c.partner_id AND g.component = c.component
    AND g.month = c.period_month
WHERE c.type = 'systematic_gap' AND c.status IN {_LIVE}
"""


def _inline(sql: str, **values: date | int | float | None) -> str:
    """Put values into a CREATE TABLE AS, which can't take bind parameters. Dates and numbers only,
    all from this module, the clock or the database, so no text from a file can reach the SQL."""
    for name, value in values.items():
        if value is None:
            literal = "NULL"
        elif isinstance(value, date):
            literal = f"'{value.isoformat()}'"
        elif isinstance(value, int | float):
            literal = repr(value)
        else:
            raise TypeError(f"{name} must be a date or a number")
        sql = sql.replace(f":{name}", literal)
    return sql


def _reset_todo(conn: Connection, select_orders: str, params: dict) -> int:
    conn.execute(text(f"DROP TABLE IF EXISTS {', '.join(_TEMP_TABLES)}"))
    conn.execute(text("CREATE TEMP TABLE recon_todo (order_id bigint PRIMARY KEY) ON COMMIT DROP"))
    count = conn.execute(text(f"INSERT INTO recon_todo {select_orders}"), params).rowcount
    conn.execute(text("ANALYZE recon_todo"))  # a fresh temp table has no statistics
    return count


def _run(conn: Connection, as_of: date) -> dict:
    for statement in (_ACTUAL, _CARDS, _CHANNEL_CARDS, _COURIER_CARDS, _ZONES, _PAID_THROUGH):
        conn.execute(text(statement))
    conn.execute(
        text(
            "ANALYZE recon_actual, recon_channel_cards, recon_courier_cards, recon_zones, "
            "recon_paid_through"
        )
    )
    scans_through = conn.execute(
        text("SELECT (max(scanned_at) AT TIME ZONE 'Asia/Kolkata')::date FROM return_scans")
    ).scalar()
    conn.execute(
        text(
            _inline(
                _CALC,
                as_of=as_of,
                scans_through=scans_through,
                settled_below=SETTLED_BELOW_PAISE,
                payment_backstop=PAYMENT_BACKSTOP_DAYS,
                return_backstop=RETURN_BACKSTOP_DAYS,
            )
        )
    )
    for table in ("order_recon", "small_gaps"):
        conn.execute(
            text(f"DELETE FROM {table} t USING recon_todo d WHERE t.order_id = d.order_id")
        )
    conn.execute(text(_WRITE_RESULTS))
    conn.execute(text(_WRITE_SMALL_GAPS))

    conn.execute(text(_ISSUES))
    updated = conn.execute(text(_UPDATE_CLAIMS)).rowcount
    opened = conn.execute(text(_OPEN_CLAIMS)).rowcount
    resolved = conn.execute(text(_RESOLVE_CLAIMS)).rowcount

    conn.execute(
        text(
            _inline(
                _PATTERNS,
                as_of=as_of,
                pattern_sum=PATTERN_SUM_PAISE,
                pattern_orders=PATTERN_MIN_ORDERS,
                pattern_share=PATTERN_ONE_WAY_SHARE,
            )
        )
    )
    conn.execute(text(_UPDATE_PATTERN_CLAIMS))
    opened += conn.execute(text(_OPEN_PATTERN_CLAIMS)).rowcount
    resolved += conn.execute(text(_RESOLVE_PATTERN_CLAIMS)).rowcount
    conn.execute(
        text(
            f"DELETE FROM claim_orders co USING claims c WHERE co.claim_id = c.id "
            f"AND c.type = 'systematic_gap' AND c.status IN {_LIVE}"
        )
    )
    conn.execute(text(_LINK_PATTERN_ORDERS))
    return {"claims_opened": opened, "claims_updated": updated, "claims_resolved": resolved}


def match_upload(
    conn: Connection,
    upload_id: int,
    kind: FileKind,
    partner_id: int,
    as_of: date | None = None,
) -> dict:
    """Re-match the orders one upload touched.

    A partner file also re-checks that partner's pending orders: a missing payment is exactly an
    order with no line in the file. A scan file does the same for returns awaiting a scan.
    """
    params = {"upload_id": upload_id, "partner_id": partner_id}
    if kind == FileKind.ORDERS:
        select_orders = "SELECT id FROM orders WHERE upload_id = :upload_id"
    elif kind == FileKind.RETURN_SCANS:
        select_orders = """
            SELECT order_id FROM return_scans
            WHERE upload_id = :upload_id AND order_id IS NOT NULL
            UNION
            SELECT o.id FROM orders o
            WHERE o.delivery_status = 'returned_to_warehouse' AND NOT EXISTS (
                SELECT 1 FROM return_scans rs WHERE rs.order_id = o.id
            )
        """
    else:
        select_orders = """
            SELECT order_id FROM partner_lines
            WHERE upload_id = :upload_id AND order_id IS NOT NULL
            UNION
            SELECT r.order_id FROM order_recon r JOIN orders o ON o.id = r.order_id
            WHERE r.status = 'pending'
              AND (o.channel_partner_id = :partner_id OR o.courier_partner_id = :partner_id)
        """
    matched = _reset_todo(conn, select_orders, params)
    return {"orders_matched": matched, **_run(conn, as_of or today())}


def match_all(conn: Connection, as_of: date | None = None) -> dict:
    """Re-match everything, for example after a rate card or a tolerance changes."""
    matched = _reset_todo(conn, "SELECT id FROM orders", {})
    return {"orders_matched": matched, **_run(conn, as_of or today())}


def match_partner(
    conn: Connection, partner_id: int, since: date | None = None, as_of: date | None = None
) -> dict:
    """A partner's orders from `since` on (all of them by default), after its rate card or
    settings change. Rate cards apply by order date, so earlier orders can't be affected."""
    select_orders = """
        SELECT id FROM orders
        WHERE (channel_partner_id = :partner OR courier_partner_id = :partner)
          AND order_date >= :since
    """
    params = {"partner": partner_id, "since": since or date.min}
    matched = _reset_todo(conn, select_orders, params)
    return {"orders_matched": matched, **_run(conn, as_of or today())}


def match_pending(conn: Connection, as_of: date | None = None) -> dict:
    """Orders waiting on a date: time alone can turn them into unpaid or missing returns."""
    select_orders = """
        SELECT order_id FROM order_recon WHERE status = 'pending'
        UNION
        SELECT o.id FROM orders o
        WHERE o.delivery_status = 'returned_to_warehouse' AND NOT EXISTS (
            SELECT 1 FROM return_scans rs WHERE rs.order_id = o.id
        )
    """
    matched = _reset_todo(conn, select_orders, {})
    return {"orders_matched": matched, **_run(conn, as_of or today())}
