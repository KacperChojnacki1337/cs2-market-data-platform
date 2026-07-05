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

-- Units still held per buy lot (FIFO: sold units removed oldest-first).
-- Replaces the old item_id-based exclusion, which dropped every lot of a
-- partially-sold item instead of only the sold units.
held as (
    select
        buy_asset_id      as asset_id,
        count(*)          as remaining_qty
    from {{ ref('int_fifo_units') }}
    where not is_sold
    group by buy_asset_id
),

coeffs as (
    select * from {{ ref('real_cash_coefficients') }}
),

skinport_prices as (
    select * from {{ ref('int_latest_skinport_prices') }}
),

volume as (
    select * from {{ ref('int_latest_volume') }}
),

final as (
    select
        a.asset_sk,
        a.asset_id,
        a.item_id,
        a.buy_date,
        a.buy_price                                                         as buy_price_pln,
        a.buy_currency,
        h.remaining_qty                                                     as quantity,
        a.quantity                                                          as buy_quantity,
        a.category,
        a.purchase_channel,

        -- Current prices
        p.price_usd                                                         as current_price_usd,
        round(p.price_usd * r.rate, 2)                                      as current_price_pln,
        p.price_fetched_at,
        r.rate                                                              as usd_pln_rate,
        r.rate_fetched_at,

        -- Portfolio value (only units still held)
        round(p.price_usd * h.remaining_qty, 2)                             as current_value_usd,
        round(p.price_usd * r.rate * h.remaining_qty, 2)                    as current_value_pln,

        -- Unrealized PnL in PLN (buy and current both in PLN)
        round((p.price_usd * r.rate) - a.buy_price, 2)                     as pnl_per_unit_pln,
        round(((p.price_usd * r.rate) - a.buy_price) * h.remaining_qty, 2) as pnl_total_pln,

        -- Unrealized PnL %
        round(
            (((p.price_usd * r.rate) - a.buy_price) / nullif(a.buy_price, 0)) * 100
        , 2)                                                                as pnl_pct,

        -- Steam net value (gross price minus 15% Steam fee)
        round(p.price_usd * h.remaining_qty * 0.85, 2)                     as net_value_steam_usd,
        round(p.price_usd * r.rate * h.remaining_qty * 0.85, 2)            as net_value_steam_pln,
        round(((p.price_usd * r.rate * 0.85) - a.buy_price) * h.remaining_qty, 2) as net_pnl_steam_pln,
        round(
            safe_divide((p.price_usd * r.rate * 0.85) - a.buy_price, a.buy_price) * 100
        , 2)                                                                as net_pnl_pct_steam,

        -- Real cash value (CSFloat sale: price_usd × rate × quantity × coeff)
        coalesce(c.real_cash_coeff, 0.65)                                   as real_cash_coeff,
        round(p.price_usd * r.rate * h.remaining_qty * coalesce(c.real_cash_coeff, 0.65), 2) as real_cash_value_pln,
        round(
            (p.price_usd * r.rate * h.remaining_qty * coalesce(c.real_cash_coeff, 0.65))
            - (a.buy_price * h.remaining_qty)
        , 2)                                                                as real_cash_pnl_pln,
        round(
            safe_divide(
                (p.price_usd * r.rate * h.remaining_qty * coalesce(c.real_cash_coeff, 0.65))
                - (a.buy_price * h.remaining_qty),
                a.buy_price * h.remaining_qty
            ) * 100
        , 2)                                                                as real_cash_pnl_pct,

        -- Steam volume (liquidity indicator)
        coalesce(v.volume_7d, 0)                                            as volume_7d,
        case
            when coalesce(v.volume_7d, 0) < 5 then 'LOW'
            when coalesce(v.volume_7d, 0) < 50 then 'MEDIUM'
            else 'HIGH'
        end                                                                 as liquidity_risk,

        -- Skinport prices (alternative market, gross — before Skinport's sale fee)
        s.skinport_price_pln,
        round(
            (s.skinport_price_pln - a.buy_price) * h.remaining_qty, 2
        )                                                                    as skinport_pnl_pln,
        round(
            safe_divide(
                (s.skinport_price_pln - a.buy_price) * h.remaining_qty,
                a.buy_price * h.remaining_qty
            ) * 100
        , 2)                                                                as skinport_pnl_pct,

        -- Skinport net value (gross price minus 8% standard Skinport sale fee)
        round(s.skinport_price_pln * h.remaining_qty * 0.92, 2)             as net_value_skinport_pln,
        round(
            (s.skinport_price_pln * 0.92 - a.buy_price) * h.remaining_qty, 2
        )                                                                    as net_skinport_pnl_pln,
        round(
            safe_divide(
                (s.skinport_price_pln * 0.92 - a.buy_price) * h.remaining_qty,
                a.buy_price * h.remaining_qty
            ) * 100
        , 2)                                                                as net_skinport_pnl_pct,

        -- Accuracy indicator: how close real_cash_coeff is to actual Skinport price
        round(
            safe_divide(
                p.price_usd * r.rate * coalesce(c.real_cash_coeff, 0.65),
                s.skinport_price_pln
            ), 4
        )                                                                    as coeff_accuracy

    from assets a
    join held h on a.asset_id = h.asset_id
    left join prices p on a.item_id = p.item_id
    left join exchange_rate r on 1 = 1
    left join coeffs c on a.category = c.category
    left join skinport_prices s on a.item_id = s.item_id
    left join volume v on a.item_id = v.item_id
)

select * from final