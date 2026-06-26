{{ config(
    materialized='table',
    partition_by={'field': 'snapshot_date', 'data_type': 'date'}
) }}

with price_dates as (
    select distinct DATE(timestamp) as snapshot_date
    from {{ source('steam_raw', 'prices_history') }}
),

all_items as (
    select distinct item_id
    from {{ ref('dim_assets') }}
),

daily_prices as (
    select
        item_id,
        price_usd,
        DATE(fetched_at) as price_date
    from {{ ref('stg_prices') }}
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
    from {{ ref('stg_exchange_rates') }}
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

sales as (
    select item_id, sell_date
    from {{ ref('stg_sales') }}
),

coeffs as (
    select * from {{ ref('real_cash_coefficients') }}
)

select
    pf.snapshot_date,
    round(sum(a.quantity * pf.price_usd), 2)                              as portfolio_value_usd,
    round(sum(a.quantity * pf.price_usd * r.usd_pln_rate), 2)            as portfolio_value_pln,
    round(sum(cast(a.buy_price as float64) * a.quantity), 2)              as total_cost_pln,
    round(
        sum(a.quantity * pf.price_usd * r.usd_pln_rate)
        - sum(cast(a.buy_price as float64) * a.quantity), 2
    )                                                                      as unrealized_pnl_pln,
    round(safe_divide(
        sum(a.quantity * pf.price_usd * r.usd_pln_rate)
            - sum(cast(a.buy_price as float64) * a.quantity),
        sum(cast(a.buy_price as float64) * a.quantity)
    ) * 100, 2)                                                            as unrealized_pnl_pct,
    count(distinct a.asset_id)                                             as active_positions,
    r.usd_pln_rate,
    round(
        sum(a.quantity * pf.price_usd * r.usd_pln_rate * coalesce(c.real_cash_coeff, 0.65))
    , 2)                                                                    as real_cash_portfolio_value_pln
from prices_filled                  pf
join {{ ref('dim_assets') }}         a   on  pf.item_id       = a.item_id
join rates_filled                    r   on  pf.snapshot_date = r.snapshot_date
left join sales                   sold   on  a.item_id        = sold.item_id
    and sold.sell_date <= pf.snapshot_date
left join coeffs                     c   on  a.category       = c.category
where sold.item_id is null
  and pf.price_usd is not null
group by pf.snapshot_date, r.usd_pln_rate