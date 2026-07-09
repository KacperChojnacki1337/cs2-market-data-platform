-- Single-row portfolio KPI summary — the metrics layer for Looker Studio.
--
-- Best practice is to keep metric logic in dbt (version-controlled, tested) rather
-- than in fragile BI calculated fields. This model owns the headline KPIs and the
-- ratio metrics (concentration, liquidity share, cash return) so every consumer
-- reads identical numbers. Looker binds one scorecard per field, no aggregation to
-- configure. Chart tiles (allocation, concentration, liquidity) still read
-- fct_portfolio directly, since those are native group-bys.

with p as (
    select * from {{ ref('fct_portfolio') }}
),

-- Concentration must be measured per item, not per lot: an item bought across
-- several lots (each its own row in fct_portfolio) would otherwise report the
-- value of its single largest lot, understating true concentration risk (and,
-- for a multi-lot item that isn't also the single biggest lot elsewhere,
-- naming the wrong item as "top position").
per_item as (
    select item_id, sum(current_value_pln) as item_value_pln
    from p
    group by item_id
)

select
    -- Cost basis and the three market valuations
    round(sum(buy_price_pln * quantity), 2)                     as cost_pln,
    round(sum(current_value_pln), 2)                            as steam_gross_pln,
    round(sum(net_value_steam_pln), 2)                          as steam_net_pln,
    round(sum(real_cash_value_pln), 2)                          as csfloat_pln,
    round(sum(net_value_skinport_pln), 2)                       as skinport_net_pln,

    -- Unrealized PnL: Steam gross (headline) and realisable cash (Skinport net)
    round(sum(pnl_total_pln), 2)                                as unrealized_pnl_steam_pln,
    round(sum(net_value_skinport_pln) - sum(buy_price_pln * quantity), 2) as unrealized_pnl_cash_pln,
    round(
        safe_divide(
            sum(net_value_skinport_pln) - sum(buy_price_pln * quantity),
            sum(buy_price_pln * quantity)
        ) * 100, 2
    )                                                           as cash_return_pct,

    -- Counts
    count(*)                                                    as positions,
    sum(quantity)                                               as units,

    -- Concentration: largest single item (summed across its lots) as a share of market value
    round((select max(item_value_pln) from per_item), 2)         as top_position_value_pln,
    round(safe_divide((select max(item_value_pln) from per_item), sum(current_value_pln)) * 100, 1) as top_position_share_pct,

    -- Liquidity: share of market value in LOW-liquidity positions
    round(
        safe_divide(
            sum(case when liquidity_risk = 'LOW' then current_value_pln end),
            sum(current_value_pln)
        ) * 100, 1
    )                                                           as low_liquidity_share_pct,

    -- Cash-vs-Steam gap (Steam net minus real cash on CSFloat)
    round(sum(net_value_steam_pln) - sum(real_cash_value_pln), 2) as steam_vs_cash_gap_pln

from p
