
  
    

    create or replace table `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
      
    
    

    
    OPTIONS()
    as (
      with sales as (
    select * from `steam-tracker-portfolio`.`steam_staging`.`stg_sales`
),

assets as (
    select * from `steam-tracker-portfolio`.`steam_marts`.`dim_assets`
),

with_fees as (
    select
        s.*,
        case s.sell_channel
            when 'Steam'    then 15.0
            when 'CSFloat'  then 2.0
            when 'Skinport' then 8.0
            else 0.0
        end as fee_pct
    from sales s
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
        s.sell_channel,
        s.sell_price                                                            as gross_sell_price_pln,
        s.fee_pct,
        round(s.sell_price * s.fee_pct / 100, 2)                               as fee_amount_pln,
        round(s.sell_price * (1 - s.fee_pct / 100), 2)                         as net_sell_price_pln,
        s.sold_at,

        DATE_DIFF(s.sell_date, a.buy_date, DAY)                                as holding_period_days,
        round(s.sell_price * (1 - s.fee_pct / 100) - a.buy_price, 2)           as realized_pnl_pln,
        round(
            safe_divide(
                s.sell_price * (1 - s.fee_pct / 100) - a.buy_price,
                a.buy_price
            ) * 100
        , 2)                                                                    as realized_pnl_pct

    from with_fees s
    join assets a on s.item_id = a.item_id
)

select * from final
    );
  