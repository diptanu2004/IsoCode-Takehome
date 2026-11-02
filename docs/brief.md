# Client brief

The problem statement this project answers, transcribed as given. The TDD
labels every point where the build goes beyond it as an assumption.

## Client

Company A, Bengaluru. D2C home textiles brand selling on its own website, 3
marketplaces and 2 quick-commerce apps. 45 staff, revenue ₹38–45 crore a year,
a finance team of 3.

## Today

Shipping runs through 2 courier aggregators, and 55% of website orders are cash
on delivery (COD). Each month finance downloads settlement, COD remittance and
freight invoice reports into Excel and matches them with lookups. Returns are
rarely checked against what reached the warehouse. Books are in Tally.

## The problem

Assumptions: 30,000 orders a month at ₹1,200 average, ₹43.2 crore a year. 1.5%
of order value leaks through unremitted COD, wrong fees, wrong weight slabs and
lost returns: ₹64.8 lakh. Finance spends 60% of its time reconciling:
3 × 60% × ₹6 lakh = ₹10.8 lakh. Total ₹75.6 lakh a year, and month-end close
takes 12 days.

## Users

Finance executives, the returns manager, and the finance co-founder, who signs
the deal.

## The ask

> We know money is leaking but we can't say where. I want to upload every
> partner's reports and see, per order, whether we were paid the right amount.
> Anything short, late or overcharged should land in a claims list with
> evidence. Returns that never arrived must be caught within days.

## Must have

- Upload of order, settlement, COD and freight files (CSV or Excel) with column
  mapping saved per source
- Order-level matching with statuses: settled, short-paid, unpaid past due,
  freight overcharged, return not received
- Rate cards per channel and courier to compute expected amounts and flag gaps
  over ₹10
- Returns tracker against warehouse receipt scans
- Claims list by partner with evidence export, and a money-at-risk dashboard by
  partner and age
- Partner-wise summary of TCS and TDS deducted

## Core build

Software Engineering candidates must ship this slice end to end, in about 6 to 8
screens: file upload with saved column mapping, order-level matching, rate-card
expected amounts, claims list, and money-at-risk view. Roles: finance executive
and co-founder. AI: column mapping suggestions. Hard part: matching 1 lakh rows
in under a minute. Mock payments, messages and outside systems, and seed from
the sample data. The rest of MUST HAVE is optional depth.

## Out of scope

- Storefront or inventory sync
- GST filing
- Automatic claim filing

## AI or automation

Matching and expected amounts are rules, not AI. AI proposes column mappings
when a partner changes its format, and suggests matches when an order ID is
missing. When it is wrong: mappings are previewed on 20 rows, approved before
saving, and versioned for rollback. Suggested matches go to a review queue and
are never closed automatically.

## Constraints

Customer names, phones and addresses are personal data under the DPDP Act 2023,
so mask them where not needed. Settlements deduct GST TCS and income tax TDS,
which must be shown separately, not as leakage. Files of 1 lakh rows should
process in under a minute. Formats change without notice.

## Design

The client values good, minimal design: clean layouts, white space, a
restrained palette and clear type, with nothing on screen that lacks a purpose.
Design counts as much as features. Finance staff work through large tables:
make differences and money at risk stand out, and keep filters simple.

## Budget and time

₹6.5 lakh, 6 weeks. Success: 98% of orders auto-matched; month-end close from
12 days to 3; at least ₹30 lakh recovered through claims in year one.

## Sample data

| Order ID     | Channel       | Value ₹ | Payment | Delivery status       | Expected ₹ | Received ₹ | Difference ₹ |
|--------------|---------------|--------:|---------|-----------------------|-----------:|-----------:|-------------:|
| ORD-W-204118 | Website       |   1,499 | COD     | Delivered             |      1,409 |          0 |       −1,409 |
| ORD-M-88120  | Marketplace 1 |   1,199 | Prepaid | Delivered             |        899 |        862 |          −37 |
| ORD-W-204377 | Website       |   2,398 | Prepaid | Returned to warehouse |        −96 |       −142 |          −46 |
