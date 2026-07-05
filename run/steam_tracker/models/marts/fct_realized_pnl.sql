
  
    

    create or replace table `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
      
    
    

    
    OPTIONS()
    as (
      -- Realized PnL on closed positions, built on FIFO unit matching.
--
-- Grain: one row per (sale x buy lot) allocation. A sale that spans multiple
-- lots produces one row per lot, each carrying that lot's own buy price and
-- holding period. Price columns are per-unit (mirroring buy_price convention);
-- realized_pnl_pln is the total for the allocated units.

with sold_units as (
    select *
    from `steam-tracker-portfolio`.`steam_staging`.`int_fifo_units`
    where is_sold
),

-- Collapse individual sold units back to (sale x lot) allocations
allocations as (
    select
        sell_id,
        buy_asset_id,
        item_id,
        category,
        purchase_channel,
        buy_date,
        sell_date,
        sold_at,
        sell_channel,
        max(buy_price)      as buy_price,
        max(sell_price)     as sell_price,
        count(*)            as units_sold_from_lot
    from sold_units
    group by
        sell_id, buy_asset_id, item_id, category, purchase_channel,
        buy_date, sell_date, sold_at, sell_channel
),

with_fees as (
    select
        a.*,
        case a.sell_channel
            when 'Steam'    then 15.0
            when 'CSFloat'  then 2.0
            when 'Skinport' then 8.0
            else 0.0
        end as fee_pct
    from allocations a
),

final as (
    select
        to_hex(md5(cast(coalesce(cast(sell_id as string), '_dbt_utils_surrogate_key_null_') || '-' || coalesce(cast(buy_asset_id as string), '_dbt_utils_surrogate_key_null_') as string))) as realized_sk,
        buy_asset_id,
        sell_id,
        item_id,
        category,
        purchase_channel,
        units_sold_from_lot,

        buy_date,
        buy_price                                                       as buy_price_pln,

        sell_date,
        sell_channel,
        sell_price                                                      as gross_sell_price_pln,
        fee_pct,
        round(sell_price * fee_pct / 100, 2)                            as fee_amount_pln,
        round(sell_price * (1 - fee_pct / 100), 2)                      as net_sell_price_pln,
        sold_at,

        DATE_DIFF(sell_date, buy_date, DAY)                             as holding_period_days,

        -- Total realized PnL for the allocated units
        round(
            (sell_price * (1 - fee_pct / 100) - buy_price) * units_sold_from_lot, 2
        )                                                               as realized_pnl_pln,

        -- Per-unit realized PnL % (quantity cancels)
        round(
            safe_divide(
                sell_price * (1 - fee_pct / 100) - buy_price,
                buy_price
            ) * 100
        , 2)                                                            as realized_pnl_pct

    from with_fees
)

select * from final
    );
  