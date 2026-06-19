{{ config(
    materialized='incremental',
    unique_key='snapshot_date',
    partition_by={'field': 'snapshot_date', 'data_type': 'date'}
) }}

with price_dates as (
    select distinct DATE(timestamp) as snapshot_date
    from {{ source('steam_raw', 'prices_history') }}
    {% if is_incremental() %}
    where DATE(timestamp) > (select max(snapshot_date) from {{ this }})
    {% endif %}
),

daily_prices as (
    select
        item_id,
        price_usd,
        DATE(fetched_at) as price_date
    from {{ ref('stg_prices') }}
    qualify ROW_NUMBER() over (
        partition by item_id, DATE(fetched_at)
        order by fetched_at desc
    ) = 1
),

daily_rates as (
    select
        rate as usd_pln_rate,
        DATE(fetched_at) as rate_date
    from {{ ref('stg_exchange_rates') }}
    qualify ROW_NUMBER() over (
        partition by DATE(fetched_at)
        order by fetched_at desc
    ) = 1
),

sales as (
    select item_id, sell_date
    from {{ ref('stg_sales') }}
)

select
    pd.snapshot_date,
    round(sum(a.quantity * p.price_usd), 2)                             as portfolio_value_usd,
    round(sum(a.quantity * p.price_usd * r.usd_pln_rate), 2)           as portfolio_value_pln,
    round(sum(cast(a.buy_price as float64) * a.quantity), 2)            as total_cost_pln,
    round(
        sum(a.quantity * p.price_usd * r.usd_pln_rate)
        - sum(cast(a.buy_price as float64) * a.quantity), 2
    )                                                                    as unrealized_pnl_pln,
    round(safe_divide(
        sum(a.quantity * p.price_usd * r.usd_pln_rate)
            - sum(cast(a.buy_price as float64) * a.quantity),
        sum(cast(a.buy_price as float64) * a.quantity)
    ) * 100, 2)                                                          as unrealized_pnl_pct,
    count(distinct a.asset_id)                                           as active_positions,
    r.usd_pln_rate
from price_dates pd
join daily_prices              p on pd.snapshot_date = p.price_date
join {{ ref('dim_assets') }}   a on p.item_id        = a.item_id
join daily_rates               r on pd.snapshot_date = r.rate_date
left join sales             sold on a.item_id        = sold.item_id
    and sold.sell_date <= pd.snapshot_date
where sold.item_id is null
group by pd.snapshot_date, r.usd_pln_rate

