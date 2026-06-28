with assets as (
    select * from {{ ref('dim_assets') }}
),

prices as (
    select * from {{ ref('int_latest_prices') }}
),

exchange_rate as (
    select * from {{ ref('int_latest_exchange_rate') }}
    where from_currency = 'USD'
    and to_currency = 'PLN'
),

sold_items as (
    select distinct item_id from {{ ref('stg_sales') }}
),

coeffs as (
    select * from {{ ref('real_cash_coefficients') }}
),

skinport_prices as (
    select * from {{ ref('int_latest_skinport_prices') }}
),

final as (
    select
        a.asset_sk,
        a.asset_id,
        a.item_id,
        a.buy_date,
        a.buy_price                                                         as buy_price_pln,
        a.buy_currency,
        a.quantity,
        a.category,
        a.purchase_channel,

        -- Current prices
        p.price_usd                                                         as current_price_usd,
        round(p.price_usd * r.rate, 2)                                      as current_price_pln,
        p.price_fetched_at,
        r.rate                                                              as usd_pln_rate,
        r.rate_fetched_at,

        -- Portfolio value
        round(p.price_usd * a.quantity, 2)                                  as current_value_usd,
        round(p.price_usd * r.rate * a.quantity, 2)                         as current_value_pln,

        -- Unrealized PnL in PLN (buy and current both in PLN)
        round((p.price_usd * r.rate) - a.buy_price, 2)                     as pnl_per_unit_pln,
        round(((p.price_usd * r.rate) - a.buy_price) * a.quantity, 2)      as pnl_total_pln,

        -- Unrealized PnL %
        round(
            (((p.price_usd * r.rate) - a.buy_price) / nullif(a.buy_price, 0)) * 100
        , 2)                                                                as pnl_pct,

        -- Steam net value (gross price minus 15% Steam fee)
        round(p.price_usd * a.quantity * 0.85, 2)                          as net_value_steam_usd,
        round(p.price_usd * r.rate * a.quantity * 0.85, 2)                 as net_value_steam_pln,
        round(((p.price_usd * r.rate * 0.85) - a.buy_price) * a.quantity, 2) as net_pnl_steam_pln,
        round(
            safe_divide((p.price_usd * r.rate * 0.85) - a.buy_price, a.buy_price) * 100
        , 2)                                                                as net_pnl_pct_steam,

        -- Real cash value (CSFloat sale: price_usd × rate × quantity × coeff)
        coalesce(c.real_cash_coeff, 0.65)                                   as real_cash_coeff,
        round(p.price_usd * r.rate * a.quantity * coalesce(c.real_cash_coeff, 0.65), 2) as real_cash_value_pln,
        round(
            (p.price_usd * r.rate * a.quantity * coalesce(c.real_cash_coeff, 0.65))
            - (a.buy_price * a.quantity)
        , 2)                                                                as real_cash_pnl_pln,
        round(
            safe_divide(
                (p.price_usd * r.rate * a.quantity * coalesce(c.real_cash_coeff, 0.65))
                - (a.buy_price * a.quantity),
                a.buy_price * a.quantity
            ) * 100
        , 2)                                                                as real_cash_pnl_pct,

        -- Skinport prices (alternative market)
        s.skinport_price_pln,
        round(
            (s.skinport_price_pln - a.buy_price) * a.quantity, 2
        )                                                                    as skinport_pnl_pln,
        round(
            safe_divide(
                (s.skinport_price_pln - a.buy_price) * a.quantity,
                a.buy_price * a.quantity
            ) * 100
        , 2)                                                                as skinport_pnl_pct,

        -- Accuracy indicator: how close real_cash_coeff is to actual Skinport price
        round(
            safe_divide(
                p.price_usd * r.rate * coalesce(c.real_cash_coeff, 0.65),
                s.skinport_price_pln
            ), 4
        )                                                                    as coeff_accuracy

    from assets a
    left join prices p on a.item_id = p.item_id
    left join exchange_rate r on 1 = 1
    left join coeffs c on a.category = c.category
    left join skinport_prices s on a.item_id = s.item_id
    where a.item_id not in (select item_id from sold_items)
)

select * from final