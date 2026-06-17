with sales as (
    select * from `steam-tracker-portfolio`.`steam_staging`.`stg_sales`
),

assets as (
    select * from `steam-tracker-portfolio`.`steam_marts`.`dim_assets`
),

final as (
    select
        a.asset_sk,
        a.asset_id                                                              as buy_asset_id,
        a.item_id,
        a.buy_date,
        a.buy_price                                                             as buy_price_pln,
        a.quantity,
        a.category,
        a.purchase_channel,

        s.sell_date,
        s.sell_price                                                            as sell_price_pln,
        s.sold_at,

        DATE_DIFF(s.sell_date, a.buy_date, DAY)                                as holding_period_days,
        round(s.sell_price - a.buy_price, 2)                                   as realized_pnl_pln,
        round(
            safe_divide(s.sell_price - a.buy_price, a.buy_price) * 100
        , 2)                                                                    as realized_pnl_pct

    from sales s
    join assets a on s.item_id = a.item_id
)

select * from final