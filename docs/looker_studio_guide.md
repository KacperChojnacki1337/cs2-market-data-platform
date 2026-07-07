# Looker Studio dashboard — build guide (#63)

Step-by-step recipe to build the CS2 portfolio dashboard in Looker Studio on top of
the `steam_marts` gold layer. The [HTML mockup](../) produced earlier is the visual
target; this guide reproduces it as a live, auto-refreshing report.

Looker Studio is a GUI tool in your Google account — there is no code to write here.
All metric logic already lives in dbt (`rpt_portfolio_summary` owns the KPIs and
ratio metrics), so Looker only presents. Reading time to build: ~45 min.

## 0. Prerequisites

- A Google account with **BigQuery Data Viewer + Job User** on project
  `steam-tracker-portfolio` (the marts live in `steam_marts`).
- Looker Studio: <https://lookerstudio.google.com> (free).

## 1. Create the data sources

For each table below: **Create → Data source → BigQuery → steam-tracker-portfolio →
steam_marts → [table] → Connect**. Repeat so you have five data sources.

| Data source | Table | Feeds |
|---|---|---|
| Summary | `rpt_portfolio_summary` | KPI scorecards, risk callouts |
| Portfolio | `fct_portfolio` | allocation, concentration, liquidity |
| Sell signals | `worth_to_sell` | sell table |
| History | `fct_portfolio_history` | value-over-time chart |
| Realized | `fct_realized_pnl` | closed-trade note |

After connecting each, check field types: PLN amounts → **Number**, `snapshot_date`
→ **Date (YMD)**, `net_profit_pct` → **Number** (not percent — it is already ×100).
Set default aggregation of the `*_pln` measures in `fct_portfolio` to **Sum**.

> Governance note: because the ratio metrics (`cash_return_pct`,
> `top_position_share_pct`, `low_liquidity_share_pct`) come pre-computed from
> `rpt_portfolio_summary`, do **not** re-derive them as Looker calculated fields —
> bind the field directly. One source of truth, in dbt.

## 2. Page setup

- **File → Theme and layout → Theme → Edit**: dark canvas `#1a1a19`, text `#f6f6f4`,
  accent `#e8763c` (CS2 economy orange). Or start from the "Constellation" dark theme.
- Layout: 12-column grid, canvas 1200×2400.
- Add a **Text** title: "CS2 Skin Portfolio — Tactical Ledger". Add a **Date** /
  data-freshness note bound to `MAX(snapshot_date)` from History.

## 3. KPI row — Scorecards (source: Summary)

Add six **Scorecard** tiles, each Metric = one field, Aggregation = **none** (the
source is already one row):

| Tile | Field | Format |
|---|---|---|
| Cost basis | `cost_pln` | PLN, 0 dp |
| Cash (Skinport) | `skinport_net_pln` | PLN, 0 dp — make this the hero (larger) |
| Cash (CSFloat) | `csfloat_pln` | PLN, 0 dp |
| Steam net | `steam_net_pln` | PLN, 0 dp |
| Unrealized (cash) | `unrealized_pnl_cash_pln` | PLN, 0 dp, conditional colour + green / − red |
| Return (cash) | `cash_return_pct` | Number, 1 dp, suffix "%" |

## 4. Risk callouts — Scorecards (source: Summary)

Three scorecards with a coloured left border (Style → Border):

- **Concentration** — `top_position_share_pct` (suffix %), red border. Subtitle text:
  "largest single position".
- **Low liquidity** — `low_liquidity_share_pct` (suffix %), amber border. "share of
  value hard to sell fast".
- **Cash vs Steam gap** — `steam_vs_cash_gap_pln` (PLN), orange border. "Steam net
  minus real cash".

## 5. Sell signals — Table (source: Sell signals)

**Add a chart → Table.** Dimensions: `item_id`, `sell_tier`, `liquidity_risk`.
Metrics: `net_profit_pln`, `net_profit_pct`, `net_value_skinport_pln`,
`pct_below_peak_30d`. Sort by `net_profit_pln` **descending**.

- **Conditional formatting** on `sell_tier`: `STRONG SELL` → green text,
  `TAKE PROFIT (illiquid)` → amber text.
- On `liquidity_risk`: LOW → red, MEDIUM → amber, HIGH → green.
- This is the actionable core — place it high, right under the KPIs.

## 6. Value over time — Time series (source: History)

**Add a chart → Time series.** Dimension: `snapshot_date`. Metrics:
`portfolio_value_pln` (accent orange, area) and `total_cost_pln` (grey, dashed line).
The gap between them is unrealized PnL. Caption: "value growth is largely inventory
being loaded — cost rises with value".

## 7. Allocation — Pie/Donut (source: Portfolio)

**Add a chart → Donut.** Dimension: `category`. Metric: `current_value_pln` (Sum).
Sort descending. Show data labels as % of total. Colour by category (fixed order:
Knife, Skin, Gloves, Sticker, Agent, Case, Other).

## 8. Concentration — Bar (source: Portfolio)

**Add a chart → Bar (horizontal).** Dimension: `item_id`. Metric:
`current_value_pln` (Sum). Sort descending, **row limit 8**. Optionally colour by
`category`. This shows the knife dominating.

## 9. Liquidity — Bar (source: Portfolio)

**Add a chart → Bar.** Dimension: `liquidity_risk`. Metric: `current_value_pln`
(Sum). Manual sort LOW → MEDIUM → HIGH. Colour LOW red / MEDIUM amber / HIGH green.

## 10. Realized note (source: Realized)

A small **Table** or scorecard: `item_id`, `realized_pnl_pln`, `sell_channel`,
`holding_period_days`. At present one closed trade — the portfolio is ~100%
unrealized (paper) gains.

## 11. Refresh & share

- **Data freshness**: Resource → Manage data sources → each → set to **1 hour** (the
  marts rebuild daily at 08:00 UTC via the dbt pipeline / Airflow).
- **Share**: top-right Share → set link access. Add a filter control (dropdown on
  `category` from Portfolio) at the top for interactive slicing.

## Field reference (what each source exposes)

- `rpt_portfolio_summary` (1 row): `cost_pln`, `steam_gross_pln`, `steam_net_pln`,
  `csfloat_pln`, `skinport_net_pln`, `unrealized_pnl_steam_pln`,
  `unrealized_pnl_cash_pln`, `cash_return_pct`, `positions`, `units`,
  `top_position_value_pln`, `top_position_share_pct`, `low_liquidity_share_pct`,
  `steam_vs_cash_gap_pln`.
- `fct_portfolio` (1 row per held lot): `item_id`, `category`, `quantity`,
  `current_value_pln`, `net_value_skinport_pln`, `real_cash_value_pln`,
  `net_value_steam_pln`, `liquidity_risk`, `volume_7d`, plus PnL columns.
- `worth_to_sell`: `item_id`, `category`, `net_profit_pln`, `net_profit_pct`,
  `net_value_skinport_pln`, `liquidity_risk`, `pct_below_peak_30d`, `past_peak`,
  `sell_tier`.
- `fct_portfolio_history` (1 row per day): `snapshot_date`, `portfolio_value_pln`,
  `total_cost_pln`, `unrealized_pnl_pln`, `active_positions`,
  `real_cash_portfolio_value_pln`.
- `fct_realized_pnl` (1 row per sale × lot): `item_id`, `realized_pnl_pln`,
  `sell_channel`, `holding_period_days`, `units_sold_from_lot`.
