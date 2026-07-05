

-- Daily portfolio snapshots with FIFO time-aware holdings.
--
-- The old model excluded an item entirely once any sale of it existed before the
-- snapshot (item_id anti-join) — wrong for multi-lot and partially-sold items.
-- Here we compute held quantity per lot as-of each date: FIFO consumes the
-- oldest lots first, so a lot's sold portion at date D is
-- (units_sold_by_D - units_in_earlier_lots), clamped to the lot size.

with price_dates as (
    select distinct DATE(timestamp) as snapshot_date
    from `steam-tracker-portfolio`.`steam_raw`.`prices_history`
),

-- Buy lots with their FIFO position range (prev_cum, prev_cum + quantity]
lots as (
    select
        asset_id,
        item_id,
        buy_date,
        buy_price,
        category,
        quantity,
        coalesce(sum(quantity) over (
            partition by item_id
            order by buy_date, asset_id
            rows between unbounded preceding and 1 preceding
        ), 0) as prev_cum
    from `steam-tracker-portfolio`.`steam_marts`.`dim_assets`
),

all_items as (
    select distinct item_id from lots
),

daily_prices as (
    select
        item_id,
        price_usd,
        DATE(fetched_at) as price_date
    from `steam-tracker-portfolio`.`steam_staging`.`stg_prices`
    where not coalesce(price_flagged, false)
    qualify ROW_NUMBER() over (
        partition by item_id, DATE(fetched_at)
        order by fetched_at desc
    ) = 1
),

daily_rates as (
    select
        rate  as usd_pln_rate,
        DATE(fetched_at) as rate_date
    from `steam-tracker-portfolio`.`steam_staging`.`stg_exchange_rates`
    qualify ROW_NUMBER() over (
        partition by DATE(fetched_at)
        order by fetched_at desc
    ) = 1
),

-- Cross join all dates × all items, then forward-fill missing prices with last known valid price
prices_filled as (
    select
        snapshot_date,
        item_id,
        LAST_VALUE(price_usd IGNORE NULLS) OVER (
            PARTITION BY item_id
            ORDER BY snapshot_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) as price_usd
    from (
        select
            pd.snapshot_date,
            ai.item_id,
            dp.price_usd
        from price_dates   pd
        cross join all_items ai
        left join daily_prices dp
            on  dp.item_id    = ai.item_id
            and dp.price_date = pd.snapshot_date
    )
),

-- Forward-fill exchange rates with last known rate when NBP data is missing
rates_filled as (
    select
        snapshot_date,
        LAST_VALUE(usd_pln_rate IGNORE NULLS) OVER (
            ORDER BY snapshot_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) as usd_pln_rate
    from (
        select
            pd.snapshot_date,
            dr.usd_pln_rate
        from price_dates pd
        left join daily_rates dr on dr.rate_date = pd.snapshot_date
    )
),

sales_agg as (
    select item_id, sell_date, sum(quantity) as sold_qty
    from `steam-tracker-portfolio`.`steam_staging`.`stg_sales`
    group by item_id, sell_date
),

-- Cumulative units sold per item as of each snapshot date
sold_by_date as (
    select
        pd.snapshot_date,
        ai.item_id,
        coalesce(sum(
            case when sa.sell_date <= pd.snapshot_date then sa.sold_qty end
        ), 0) as sold_by_d
    from price_dates pd
    cross join all_items ai
    left join sales_agg sa on sa.item_id = ai.item_id
    group by pd.snapshot_date, ai.item_id
),

-- Held units per lot per snapshot (FIFO: oldest lots consumed first)
lot_snapshots as (
    select
        pd.snapshot_date,
        l.item_id,
        l.asset_id,
        l.buy_price,
        l.category,
        case
            when l.buy_date <= pd.snapshot_date
            then l.quantity - least(l.quantity, greatest(0, sd.sold_by_d - l.prev_cum))
            else 0
        end as held_qty
    from price_dates pd
    cross join lots l
    join sold_by_date sd
        on  sd.item_id       = l.item_id
        and sd.snapshot_date = pd.snapshot_date
),

coeffs as (
    select * from `steam-tracker-portfolio`.`steam_raw`.`real_cash_coefficients`
)

select
    ls.snapshot_date,
    round(sum(ls.held_qty * pf.price_usd), 2)                            as portfolio_value_usd,
    round(sum(ls.held_qty * pf.price_usd * r.usd_pln_rate), 2)           as portfolio_value_pln,
    round(sum(cast(ls.buy_price as float64) * ls.held_qty), 2)           as total_cost_pln,
    round(
        sum(ls.held_qty * pf.price_usd * r.usd_pln_rate)
        - sum(cast(ls.buy_price as float64) * ls.held_qty), 2
    )                                                                     as unrealized_pnl_pln,
    round(safe_divide(
        sum(ls.held_qty * pf.price_usd * r.usd_pln_rate)
            - sum(cast(ls.buy_price as float64) * ls.held_qty),
        sum(cast(ls.buy_price as float64) * ls.held_qty)
    ) * 100, 2)                                                           as unrealized_pnl_pct,
    count(distinct case when ls.held_qty > 0 then ls.asset_id end)       as active_positions,
    r.usd_pln_rate,
    round(
        sum(ls.held_qty * pf.price_usd * r.usd_pln_rate * coalesce(c.real_cash_coeff, 0.65))
    , 2)                                                                  as real_cash_portfolio_value_pln
from lot_snapshots                   ls
join prices_filled                   pf   on  pf.item_id       = ls.item_id
    and pf.snapshot_date = ls.snapshot_date
join rates_filled                    r    on  r.snapshot_date  = ls.snapshot_date
left join coeffs                     c    on  ls.category      = c.category
where ls.held_qty > 0
  and pf.price_usd is not null
group by ls.snapshot_date, r.usd_pln_rate