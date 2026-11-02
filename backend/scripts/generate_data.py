"""Generate Company A's partner reports, with planted leakage and an answer key.

    uv run python -m scripts.generate_data --orders 60000 --out ../var/data

Deterministic for a given --seed and --as-of. Writes into --out:

    seed/          one file per partner report, in that partner's format (scripts/catalog.py)
    demo/          files held back for a live demo: Marketplace 1's report in a changed format,
                   and late COD remittances that should close open claims
    truth.csv      every planted problem and the claim it should produce
    manifest.json  parameters, row counts and leakage totals

All people, phone numbers and addresses are invented. Phone numbers start with 5, which no
Indian mobile number does.
"""

import argparse
import csv
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import xlsxwriter

from app.enums import FileKind
from scripts.catalog import (
    COURIER_A,
    COURIER_B,
    M1,
    M1_NEW_FORMAT,
    M2,
    M3,
    PARTNER_BY_NAME,
    QC1,
    QC2,
    RATE_CARDS,
    SEED_FORMATS,
    WEBSITE,
    FileFormat,
)

MARKETPLACES = (M1, M2, M3)
QUICK_COMMERCE = (QC1, QC2)
CHANNEL_SHARE = {WEBSITE: 40, M1: 20, M2: 15, M3: 10, QC1: 9, QC2: 6}  # [A23]
ZONE_SHARE = {"A": 10, "B": 20, "C": 35, "D": 25, "E": 10}
PRICES = (399, 499, 599, 699, 799, 899, 999, 1199, 1299, 1499, 1799, 1999, 2499)
PRICE_WEIGHTS = (6, 8, 8, 10, 10, 10, 10, 10, 7, 7, 4, 3, 2)  # average order ≈ ₹1,200

# How often each problem is planted (share of the rows where it can occur).
RATES = {
    "cancelled": 0.02,
    "website_cod": 0.55,  # from the brief
    "rto_cod": 0.18,
    "return_prepaid": 0.06,
    "return_marketplace": 0.10,
    "return_quick_commerce": 0.015,
    "unremitted_cod": 0.05,
    "late_remittance": 0.25,  # of unremitted Courier A orders, paid in the demo file
    "unpaid_settlement": 0.007,
    "missing_order_id": 0.0015,
    "commission_overcharge": 0.03,
    "short_adjustment": 0.005,
    "overpaid": 0.001,
    "short_cod": 0.005,
    "weight_slab": 0.06,
    "lost_return": 0.05,
    "m2_shipping_noise": 0.08,
}

# The brief's three sample orders, reproduced exactly (TDD §8.8).
SAMPLE_IDS = {"ORD-W-204118", "ORD-M-88120", "ORD-W-204377"}


# --- Money -------------------------------------------------------------------


def half_up(amount: Decimal) -> int:
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def paise(rupees: float) -> int:
    return half_up(Decimal(str(rupees)) * 100)


def pct_of(amount: int, pct: float) -> int:
    return half_up(Decimal(amount) * Decimal(str(pct)) / 100)


def round_to_rupee(amount: int) -> int:
    return half_up(Decimal(amount) / 100) * 100


def slab_count(weight_g: int, slab_g: int) -> int:
    return max(1, math.ceil(weight_g / slab_g))


def slab_price(weight_g: int, slab_g: int, first: float, additional: float) -> int:
    return paise(first) + (slab_count(weight_g, slab_g) - 1) * paise(additional)


def freight(courier: str, zone: str, weight_g: int) -> int:
    card = RATE_CARDS[courier]
    rate = card["forward"][zone]
    return slab_price(weight_g, card["slab_g"], rate["first"], rate["additional"])


def cod_fee(courier: str, value: int) -> int:
    fee = RATE_CARDS[courier]["cod_fee"]
    return max(paise(fee["min"]), pct_of(value, fee["pct"]))


def tcs_on(value: int) -> int:
    """0.5% GST TCS on the value before 5% GST (seed values; the app takes TCS as reported)."""
    return half_up(Decimal(value) / Decimal("1.05") * Decimal("0.005"))


def tds_on(value: int) -> int:
    return half_up(Decimal(value) * Decimal("0.001"))


# --- Orders ------------------------------------------------------------------


@dataclass
class Order:
    order_id: str
    channel: str
    order_date: date
    value: int
    units: int
    payment_mode: str
    weight_g: int
    customer_name: str
    phone: str
    address: str
    pincode: str
    courier: str | None = None
    awb: str | None = None
    zone: str | None = None
    cancelled: bool = False
    shipped: date | None = None
    delivered: date | None = None
    return_started: date | None = None
    returned: date | None = None
    sample: bool = False

    def status(self, as_of: date) -> str:
        if self.cancelled:
            return "cancelled"
        if self.shipped is None or self.shipped > as_of:
            return "pending"
        if self.return_started and self.return_started <= as_of:
            return "returned_to_warehouse" if self.returned <= as_of else "return_in_transit"
        if self.delivered and self.delivered <= as_of:
            return "delivered"
        return "in_transit"


@dataclass
class Truth:
    order_id: str
    partner: str
    issue: str
    amount: int  # paise; the claim this should produce
    detail: str


@dataclass
class Dataset:
    as_of: date
    orders: list[Order] = field(default_factory=list)
    rows: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    demo_rows: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    truth: list[Truth] = field(default_factory=list)
    noise: Counter = field(default_factory=Counter)


FIRST_NAMES = (
    "Aarav",
    "Diya",
    "Kabir",
    "Meera",
    "Rohan",
    "Ananya",
    "Ishaan",
    "Kavya",
    "Arjun",
    "Saanvi",
    "Vihaan",
    "Tara",
    "Neel",
    "Riya",
    "Dev",
    "Nisha",
    "Aditya",
    "Pooja",
)
LAST_NAMES = (
    "Rao",
    "Iyer",
    "Sharma",
    "Reddy",
    "Nair",
    "Gupta",
    "Menon",
    "Patel",
    "Das",
    "Shetty",
    "Kulkarni",
    "Joshi",
    "Bhat",
    "Singh",
    "Verma",
    "Pillai",
)
AREAS = (
    "Indiranagar",
    "Koramangala",
    "Jayanagar",
    "Whitefield",
    "HSR Layout",
    "Malleshwaram",
    "Banashankari",
    "Hebbal",
    "Yelahanka",
    "Rajajinagar",
)


class Generator:
    def __init__(self, as_of: date, n_orders: int, days: int, seed: int):
        self.as_of = as_of
        self.n_orders = n_orders
        self.days = days
        self.rng = random.Random(seed)
        self.data = Dataset(as_of=as_of)
        self._counters = {
            WEBSITE: 200000,
            M1: 80000,
            QC1: 1000000,
            COURIER_A: 4000000000,
            COURIER_B: 790000000000,
        }
        self._used: set[str] = set(SAMPLE_IDS)

    # --- ids and people ---

    def _next(self, key: str) -> int:
        self._counters[key] += 1
        return self._counters[key]

    def _unique(self, make) -> str:
        while (value := make()) in self._used:
            pass
        self._used.add(value)
        return value

    def _order_id(self, channel: str) -> str:
        r = self.rng
        makers = {
            WEBSITE: lambda: f"ORD-W-{self._next(WEBSITE)}",
            M1: lambda: f"ORD-M-{self._next(M1)}",
            M2: lambda: f"40{r.randrange(10**8):08d}",
            M3: lambda: "M3-" + "".join(r.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=8)),
            QC1: lambda: f"QC1-{self._next(QC1)}",
            QC2: lambda: f"7{r.randrange(10**8):08d}",
        }
        return self._unique(makers[channel])

    def _awb(self, courier: str) -> str:
        if courier == COURIER_A:
            return self._unique(lambda: f"CA{self._next(COURIER_A)}")
        return self._unique(lambda: str(self._next(COURIER_B)))

    def _person(self) -> dict:
        r = self.rng
        return {
            "customer_name": f"{r.choice(FIRST_NAMES)} {r.choice(LAST_NAMES)}",
            "phone": f"5{r.randrange(10**9):09d}",
            "address": f"{r.randint(1, 400)}, Road No. {r.randint(1, 20)}, {r.choice(AREAS)}, "
            "Bengaluru",
            "pincode": str(r.randint(560001, 560100)),
        }

    # --- orders and their lifecycle ---

    def build_orders(self) -> None:
        r = self.rng
        channels, shares = zip(*CHANNEL_SHARE.items(), strict=True)
        for _ in range(self.n_orders):
            channel = r.choices(channels, shares)[0]
            units = r.choices((1, 2, 3), (83, 15, 2))[0]
            price = r.choices(PRICES, PRICE_WEIGHTS)[0]
            order = Order(
                order_id=self._order_id(channel),
                channel=channel,
                order_date=self.as_of - timedelta(days=r.randint(1, self.days)),
                value=paise(price) * units,
                units=units,
                payment_mode=self._payment_mode(channel),
                weight_g=units * (180 + price * 3 // 10 + r.randint(-40, 40)),
                **self._person(),
            )
            self._plan_lifecycle(order)
            self.data.orders.append(order)
        self.data.orders.extend(self._samples())
        self.data.orders.sort(key=lambda o: (o.order_date, o.order_id))

    def _payment_mode(self, channel: str) -> str:
        cod_share = {WEBSITE: RATES["website_cod"], M1: 0.3, M2: 0.3, M3: 0.35, QC1: 0.1, QC2: 0.1}[
            channel
        ]
        return "cod" if self.rng.random() < cod_share else "prepaid"

    def _plan_lifecycle(self, o: Order) -> None:
        r = self.rng
        if r.random() < RATES["cancelled"]:
            o.cancelled = True
            return
        if o.channel in QUICK_COMMERCE:
            o.shipped = o.delivered = o.order_date
        else:
            o.shipped = o.order_date + timedelta(days=1)
            arrives = o.shipped + timedelta(days=r.randint(2, 6))
        if o.channel == WEBSITE:
            o.courier = r.choices((COURIER_A, COURIER_B), (60, 40))[0]
            o.awb = self._awb(o.courier)
            o.zone = r.choices(list(ZONE_SHARE), list(ZONE_SHARE.values()))[0]
            if o.payment_mode == "cod" and r.random() < RATES["rto_cod"]:
                # Refused at the door: returned to origin, never delivered.
                o.return_started = arrives
                o.returned = arrives + timedelta(days=r.randint(4, 9))
                return
        if o.channel not in QUICK_COMMERCE:
            o.delivered = arrives
        return_rate = {
            WEBSITE: RATES["return_prepaid"],
            M1: RATES["return_marketplace"],
            M2: RATES["return_marketplace"],
            M3: RATES["return_marketplace"],
            QC1: RATES["return_quick_commerce"],
            QC2: RATES["return_quick_commerce"],
        }[o.channel]
        # Delivered website COD orders are not returned (refusals are handled above as RTO).
        can_return = o.payment_mode == "prepaid" or o.channel != WEBSITE
        if can_return and r.random() < return_rate:
            o.return_started = o.delivered + timedelta(days=2)
            o.returned = o.delivered + timedelta(days=r.randint(5, 14))

    def _samples(self) -> list[Order]:
        a = self.as_of
        w118 = Order(
            "ORD-W-204118",
            WEBSITE,
            a - timedelta(days=24),
            paise(1499),
            1,
            "cod",
            450,
            courier=COURIER_A,
            awb="CA4000204118",
            zone="D",
            sample=True,
            **self._person(),
        )
        w118.shipped, w118.delivered = a - timedelta(days=23), a - timedelta(days=20)
        m120 = Order(
            "ORD-M-88120",
            M1,
            a - timedelta(days=22),
            paise(1199),
            1,
            "prepaid",
            400,
            sample=True,
            **self._person(),
        )
        m120.shipped, m120.delivered = a - timedelta(days=21), a - timedelta(days=18)
        w377 = Order(
            "ORD-W-204377",
            WEBSITE,
            a - timedelta(days=34),
            paise(2398),
            2,
            "prepaid",
            480,
            courier=COURIER_B,
            awb="790000204377",
            zone="C",
            sample=True,
            **self._person(),
        )
        w377.shipped, w377.delivered = a - timedelta(days=33), a - timedelta(days=30)
        w377.return_started, w377.returned = a - timedelta(days=28), a - timedelta(days=15)
        return [w118, m120, w377]

    # --- partner reports ---

    def build_reports(self) -> None:
        self._systematic: Counter[date] = Counter()
        for o in self.data.orders:
            if o.cancelled or o.status(self.as_of) == "pending":
                continue
            if o.channel in MARKETPLACES or o.channel in QUICK_COMMERCE:
                self._settlement(o)
            if o.channel == WEBSITE:
                self._courier_lines(o)
            self._return_scan(o)
        for month, count in sorted(self._systematic.items()):
            self.data.truth.append(
                Truth(
                    "",
                    QC2,
                    "systematic_gap",
                    count * paise(5),
                    f"{month:%b %Y}: fixed fee ₹15 instead of ₹10 on {count} orders",
                )
            )

    def _returned(self, o: Order) -> bool:
        return o.returned is not None and o.returned <= self.as_of

    def _settlement(self, o: Order) -> None:
        r, card = self.rng, RATE_CARDS[o.channel]
        partner = PARTNER_BY_NAME[o.channel]
        returned = self._returned(o)
        if o.return_started and not returned:
            return  # partners settle a returned order once the return completes
        if not returned and (o.delivered is None or o.delivered > self.as_of):
            return
        base = o.returned if returned else o.delivered
        due = base + timedelta(days=partner.settlement_cycle_days)
        settled_on = due - timedelta(days=r.randint(0, 2))
        if settled_on > self.as_of:
            return

        row = self._settlement_amounts(o, card, returned)
        if not any(row.values()):
            return  # e.g. a quick-commerce return: nothing charged, nothing paid
        expected_net = sum(row.values()) - row["tcs"] - row["tds"]
        order_ref = o.order_id

        issue = None
        if o.order_id == "ORD-M-88120":
            row["commission"] = -paise(210.09)  # charged above the rate card
            self._truth(o, o.channel, "short_paid", paise(30.09), "commission above rate card")
            issue = "short_paid"
        elif not o.sample:
            issue = self._plant_settlement(o, row, returned, due, expected_net)
            if issue == "unpaid_past_due":
                return
            if issue == "missing_order_id":
                order_ref = ""

        if issue is None and o.channel == QC2 and not returned:
            row["fixed_fee"] -= paise(5)  # every order, below the ₹10 tolerance
            self._systematic[settled_on.replace(day=1)] += 1
        noisy = issue is None and o.channel == M2 and row["shipping_fee"]
        if noisy and r.random() < RATES["m2_shipping_noise"]:
            row["shipping_fee"] += r.choice((-200, -100, 100, 200))  # ±₹1–2, both directions
            self.data.noise["m2_shipping_noise_rows"] += 1

        row.update(
            order_ref=order_ref,
            txn_date=settled_on,
            net=sum(row[k] for k in AMOUNT_KEYS),
            reference=f"{o.channel[0]}{o.channel[-1]}-{settled_on:%y%m%d}-{r.randrange(10**6):06d}",
        )
        if o.channel == M1 and settled_on == self.as_of:
            # Today's payout arrives in Marketplace 1's new format; it is not due before today.
            self.data.demo_rows[M1_NEW_FORMAT.filename].append(row)
        else:
            self.data.rows[o.channel].append(row)

    def _settlement_amounts(self, o: Order, card: dict, returned: bool) -> dict:
        ship_cfg = card["shipping"]
        ship = slab_price(o.weight_g, **ship_cfg) if ship_cfg else 0
        if returned:
            ret = pct_of(ship, card["on_return"]["return_shipping_pct_of_forward"])
            return {
                "sale": 0,
                "commission": 0,
                "fixed_fee": 0,
                "shipping_fee": -ship,
                "return_shipping_fee": -ret,
                "tcs": 0,
                "tds": 0,
                "adjustment": 0,
            }
        commission = pct_of(o.value, card["commission_pct"])
        if card["commission_rounding"] == "rupee" or o.channel == M3:
            # Marketplace 3 rounds to the rupee although its card says paise: sub-₹1 noise.
            commission = round_to_rupee(commission)
        return {
            "sale": o.value,
            "commission": -commission,
            "fixed_fee": -paise(card["fixed_fee"]),
            "shipping_fee": -ship,
            "return_shipping_fee": 0,
            "tcs": -tcs_on(o.value),
            "tds": -tds_on(o.value),
            "adjustment": 0,
        }

    def _plant_settlement(self, o, row, returned, due, expected_net) -> str | None:
        """Maybe plant one problem in a settlement row; returns what was planted."""
        r, past_due = self.rng, due < self.as_of
        roll, cut = r.random(), 0.0
        for planted in (
            "unpaid_settlement",
            "missing_order_id",
            "commission_overcharge",
            "short_adjustment",
            "overpaid",
        ):
            cut += RATES[planted]
            if roll < cut:
                break
        else:
            return None

        if planted == "unpaid_settlement" and past_due:
            self._truth(o, o.channel, "unpaid_past_due", expected_net, "no settlement line")
            return "unpaid_past_due"
        if planted == "missing_order_id" and past_due and o.channel in MARKETPLACES:
            self._truth(
                o, o.channel, "unpaid_past_due", expected_net, "settlement line has no order ID"
            )
            return "missing_order_id"
        if planted == "commission_overcharge" and not returned:
            pct = round(RATE_CARDS[o.channel]["commission_pct"] + r.uniform(1.5, 4.0), 2)
            charged = pct_of(o.value, pct)
            delta = charged + row["commission"]
            if delta > 1000:  # above the ₹10 tolerance, so it is a claim
                row["commission"] = -charged
                self._truth(o, o.channel, "short_paid", delta, f"commission charged at {pct}%")
                return "short_paid"
        if planted == "short_adjustment":
            amount = paise(r.randint(100, 400))
            row["adjustment"] = -amount
            self._truth(o, o.channel, "short_paid", amount, "unexplained deduction")
            return "short_paid"
        if planted == "overpaid":
            amount = paise(r.randint(50, 200))
            row["adjustment"] = amount
            self._truth(o, o.channel, "overpaid", amount, "paid more than expected")
            return "overpaid"
        return None

    def _courier_lines(self, o: Order) -> None:
        courier = o.courier
        returned = self._returned(o)
        fwd = freight(courier, o.zone, o.weight_g)

        if o.payment_mode == "cod" and o.delivered and o.delivered <= self.as_of:
            self._cod_remittance(o, fwd)
        if o.payment_mode == "prepaid" and o.shipped <= self.as_of:
            self._invoice_leg(o, "forward", o.shipped, fwd)
        if returned:
            if o.payment_mode == "cod":  # refused COD: forward leg billed with the return
                self._invoice_leg(o, "forward", o.returned, fwd)
            ret = pct_of(fwd, RATE_CARDS[courier]["return_pct_of_forward"])
            self._invoice_leg(o, "return", o.returned, ret)

    def _billed_weight(self, o: Order, leg: str) -> tuple[int, int]:
        """Billed weight and the extra freight it causes (0 unless a heavier slab is planted)."""
        card = RATE_CARDS[o.courier]
        slab = card["slab_g"]
        if o.order_id == "ORD-W-204377":
            bump = True
        elif o.sample:
            bump = False
        else:
            bump = self.rng.random() < RATES["weight_slab"]
        if not bump:
            return o.weight_g, 0
        billed = slab_count(o.weight_g, slab) * slab + self.rng.randint(20, 300)
        extra = freight(o.courier, o.zone, billed) - freight(o.courier, o.zone, o.weight_g)
        if leg == "return":
            extra = pct_of(extra, card["return_pct_of_forward"])
        return billed, extra

    def _cod_remittance(self, o: Order, fwd: int) -> None:
        r = self.rng
        due = o.delivered + timedelta(days=PARTNER_BY_NAME[o.courier].settlement_cycle_days)
        remitted_on = due - timedelta(days=r.randint(0, 2))
        fee = cod_fee(o.courier, o.value)
        expected_net = o.value - fwd - fee
        if o.order_id == "ORD-W-204118":
            self._truth(o, o.courier, "unpaid_past_due", expected_net, "COD not remitted")
            return
        if remitted_on > self.as_of:
            return
        if not o.sample and due < self.as_of and r.random() < RATES["unremitted_cod"]:
            self._truth(o, o.courier, "unpaid_past_due", expected_net, "COD not remitted")
            if o.courier == COURIER_A and r.random() < RATES["late_remittance"]:
                late = self._remittance_row(o, fwd, fee, self.as_of, o.weight_g, o.value)
                self.data.demo_rows["couriera_cod_remittance_late.csv"].append(late)
            return

        billed, extra = self._billed_weight(o, "forward")
        if extra:
            self._truth(o, o.courier, "freight_overcharged", extra, f"billed {billed} g")
        collected = o.value
        if not o.sample and r.random() < RATES["short_cod"]:
            short = paise(r.randint(100, 300))
            collected -= short
            self._truth(o, o.courier, "short_paid", short, "remitted less than collected")
        row = self._remittance_row(o, fwd + extra, fee, remitted_on, billed, collected)
        self.data.rows[(o.courier, FileKind.COD_REMITTANCE)].append(row)

    def _remittance_row(self, o, freight_paise, fee, on, billed, collected) -> dict:
        return {
            "awb": o.awb,
            "order_ref": o.order_id,
            "delivered_on": o.delivered.strftime("%d/%m/%Y"),
            "sale": collected,
            "freight_forward": -freight_paise,
            "cod_fee": -fee,
            "net": collected - freight_paise - fee,
            "txn_date": on,
            "reference": f"UTR{on:%y%m%d}{self.rng.randrange(10**8):08d}",
            "billed_weight": billed,
            "zone": o.zone,
        }

    def _invoice_leg(self, o: Order, leg: str, event: date, amount: int) -> None:
        invoiced_on = event + timedelta(days=7 - event.weekday())  # the following Monday
        if invoiced_on > self.as_of:
            return
        billed, extra = self._billed_weight(o, leg)
        if extra:
            self._truth(o, o.courier, "freight_overcharged", extra, f"{leg} leg billed {billed} g")
        prefix = "CA" if o.courier == COURIER_A else "CB"
        self.data.rows[(o.courier, FileKind.FREIGHT_INVOICE)].append(
            {
                "reference": f"{prefix}-INV-{invoiced_on:%Y%m%d}",
                "txn_date": invoiced_on,
                "awb": o.awb,
                "order_ref": o.order_id,
                "charge_type": leg,
                "zone": o.zone,
                "billed_weight": billed,
                "amount": -(amount + extra),
            }
        )

    def _return_scan(self, o: Order) -> None:
        r = self.rng
        if not self._returned(o):
            return
        window = PARTNER_BY_NAME[o.channel if o.channel != WEBSITE else o.courier]
        overdue = o.returned + timedelta(days=window.return_window_days) < self.as_of
        if not o.sample and overdue and r.random() < RATES["lost_return"]:
            owner = o.courier if o.channel == WEBSITE else o.channel
            self._truth(o, owner, "return_not_received", o.value, "no warehouse scan")
            return
        scanned = datetime.combine(
            o.returned + timedelta(days=r.choice((0, 0, 1))),
            time(r.randint(9, 18), r.randint(0, 59)),
        )
        if scanned.date() > self.as_of:
            return
        self.data.rows[WAREHOUSE_KEY].append(
            {
                "scanned_at": scanned,
                "awb": o.awb,
                "order_ref": o.order_id,
                "condition": r.choices(("Good", "Damaged", "Wrong item"), (92, 6, 2))[0],
                "scanned_by": f"W-{r.randint(1, 12):02d}",
                "bin": f"R{r.randint(1, 8)}-{r.randint(1, 30):02d}",
            }
        )

    def _truth(self, o: Order, partner: str, issue: str, amount: int, detail: str) -> None:
        self.data.truth.append(Truth(o.order_id, partner, issue, amount, detail))


AMOUNT_KEYS = (
    "sale",
    "commission",
    "fixed_fee",
    "shipping_fee",
    "return_shipping_fee",
    "tcs",
    "tds",
    "adjustment",
)
WAREHOUSE_KEY = "warehouse"


# --- Writing files -----------------------------------------------------------


def _indian_grouping(rupees: int) -> str:
    s = str(rupees)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def _money_text(amount: int, style: str) -> str:
    if style == "rupee":
        whole, frac = divmod(abs(amount), 100)
        text = f"₹{_indian_grouping(whole)}.{frac:02d}"
        return f"({text})" if amount < 0 else text
    return f"{amount / 100:.2f}" if amount else "0.00"


def _cell(fmt: FileFormat, col, value, excel: bool):
    """Render one value the way this partner's file shows it."""
    if value is None or value == "":
        return None if excel else ""
    if col.values:
        value = {v: k for k, v in col.values.items()}[value]
    if col.key in MONEY_KEYS:
        amount = -value if col.negate else value
        return amount / 100 if excel else _money_text(amount, fmt.money)
    if col.key in ("billed_weight", "declared_weight") and col.scale:
        return f"{value / col.scale:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, date | datetime):
        if excel and not col.date_format:
            return value
        return value.strftime(col.date_format or "%Y-%m-%d")
    if excel and col.key in fmt.id_as_number:
        return int(value)
    return value


MONEY_KEYS = {*AMOUNT_KEYS, "value", "net", "freight_forward", "cod_fee", "amount"}


def _title_rows(fmt: FileFormat, rows: list[dict], as_of: date) -> list[str]:
    dates = [r["txn_date"] for r in rows if "txn_date" in r] or [as_of]
    period = f"Period: {min(dates):%d %b %Y} to {max(dates):%d %b %Y}"
    return [t.replace("{period}", period) for t in fmt.title_rows]


def write_file(fmt: FileFormat, rows: list[dict], path: Path, as_of: date) -> None:
    headers = [c.header for c in fmt.columns]
    titles = _title_rows(fmt, rows, as_of)
    if fmt.excel:
        wb = xlsxwriter.Workbook(path, {"constant_memory": True})
        ws = wb.add_worksheet("Report")
        date_cell = wb.add_format({"num_format": "dd-mm-yyyy"})
        bold = wb.add_format({"bold": True})
        for i, title in enumerate(titles):
            ws.write(i, 0, title, bold if i == 0 else None)
        ws.write_row(len(titles), 0, headers, bold)
        for i, row in enumerate(rows, start=len(titles) + 1):
            for j, col in enumerate(fmt.columns):
                value = _cell(fmt, col, row.get(col.key), excel=True)
                if isinstance(value, date):
                    ws.write_datetime(i, j, datetime.combine(value, time()), date_cell)
                elif value is not None:
                    ws.write(i, j, value)
        wb.close()
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for title in titles:
            writer.writerow([title] if title else [])
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_cell(fmt, c, row.get(c.key), excel=False) for c in fmt.columns])


def _order_row(o: Order, as_of: date) -> dict:
    status = o.status(as_of)
    return {
        "order_id": o.order_id,
        "channel": o.channel,
        "order_date": o.order_date,
        "customer_name": o.customer_name,
        "phone": o.phone,
        "address": o.address,
        "pincode": o.pincode,
        "payment_mode": o.payment_mode,
        "value": o.value,
        "units": o.units,
        "declared_weight": o.weight_g,
        "courier": o.courier,
        "awb": o.awb,
        "zone": o.zone,
        "delivery_status": status,
        "delivered_at": o.delivered if o.delivered and o.delivered <= as_of else None,
        "returned_at": o.returned if status == "returned_to_warehouse" else None,
    }


def _rows_for(fmt: FileFormat, data: Dataset) -> list[dict]:
    if fmt.file_kind == FileKind.ORDERS:
        return [_order_row(o, data.as_of) for o in data.orders]
    if fmt.file_kind == FileKind.SETTLEMENT:
        rows = data.rows[fmt.partner]
    elif fmt.file_kind == FileKind.RETURN_SCANS:
        rows = data.rows[WAREHOUSE_KEY]
        return sorted(rows, key=lambda r: r["scanned_at"])
    else:
        rows = data.rows[(fmt.partner, fmt.file_kind)]
    return sorted(rows, key=lambda r: (r["txn_date"], r.get("order_ref") or ""))


def write_dataset(data: Dataset, out: Path, params: dict) -> dict:
    seed_dir, demo_dir = out / "seed", out / "demo"
    seed_dir.mkdir(parents=True, exist_ok=True)
    demo_dir.mkdir(parents=True, exist_ok=True)

    files = {}
    for fmt in SEED_FORMATS:
        rows = _rows_for(fmt, data)
        write_file(fmt, rows, seed_dir / fmt.filename, data.as_of)
        files[f"seed/{fmt.filename}"] = {"source": fmt.source_name, "rows": len(rows)}

    demo_m1 = sorted(data.demo_rows[M1_NEW_FORMAT.filename], key=lambda r: r["order_ref"])
    for row in demo_m1:
        row["batch"] = f"B{data.as_of:%Y%m%d}"
    write_file(M1_NEW_FORMAT, demo_m1, demo_dir / M1_NEW_FORMAT.filename, data.as_of)
    files[f"demo/{M1_NEW_FORMAT.filename}"] = {
        "source": M1_NEW_FORMAT.source_name,
        "rows": len(demo_m1),
    }

    courier_a_cod = next(
        f for f in SEED_FORMATS if f.partner == COURIER_A and f.file_kind == FileKind.COD_REMITTANCE
    )
    late = data.demo_rows["couriera_cod_remittance_late.csv"]
    write_file(courier_a_cod, late, demo_dir / "couriera_cod_remittance_late.csv", data.as_of)
    files["demo/couriera_cod_remittance_late.csv"] = {
        "source": courier_a_cod.source_name,
        "rows": len(late),
    }

    with (out / "truth.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "partner", "issue", "amount", "detail"])
        for t in data.truth:
            writer.writerow([t.order_id, t.partner, t.issue, f"{t.amount / 100:.2f}", t.detail])

    gmv = sum(o.value for o in data.orders if not o.cancelled)
    claimable = [t for t in data.truth if t.issue != "overpaid"]
    by_issue = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for t in data.truth:
        by_issue[t.issue]["count"] += 1
        by_issue[t.issue]["amount"] += t.amount / 100
    manifest = {
        **params,
        "orders": len(data.orders),
        "orders_by_channel": dict(Counter(o.channel for o in data.orders)),
        "average_order_value": round(gmv / 100 / max(1, len(data.orders)), 2),
        "gmv": gmv / 100,
        "planted": {
            k: {"count": v["count"], "amount": round(v["amount"], 2)}
            for k, v in sorted(by_issue.items())
        },
        "planted_leakage": round(sum(t.amount for t in claimable) / 100, 2),
        "planted_leakage_pct": round(100 * sum(t.amount for t in claimable) / max(1, gmv), 2),
        "noise": dict(data.noise),
        "files": files,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))
    return manifest


def generate(as_of: date, n_orders: int, days: int, seed: int) -> Dataset:
    gen = Generator(as_of, n_orders, days, seed)
    gen.build_orders()
    gen.build_reports()
    return gen.data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--orders", type=int, default=60000)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("../var/data"))
    args = parser.parse_args()

    data = generate(args.as_of, args.orders, args.days, args.seed)
    params = {"as_of": args.as_of.isoformat(), "seed": args.seed, "days": args.days}
    manifest = write_dataset(data, args.out, params)
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in (
                    "orders",
                    "average_order_value",
                    "gmv",
                    "planted_leakage",
                    "planted_leakage_pct",
                    "planted",
                )
            },
            indent=2,
        )
    )
    print(f"Files written to {args.out.resolve()}")


if __name__ == "__main__":
    main()
