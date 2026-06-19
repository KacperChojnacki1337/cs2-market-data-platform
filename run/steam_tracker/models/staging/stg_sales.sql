

  create or replace view `steam-tracker-portfolio`.`steam_staging`.`stg_sales`
  OPTIONS()
  as with source as (
    select * from `steam-tracker-portfolio`.`steam_raw`.`sales_history`
),

renamed as (
    select
        asset_id,
        item_id,
        cast(sell_date as date)                        as sell_date,
        cast(sell_price as numeric)                    as sell_price,
        upper(sell_currency)                           as sell_currency,
        coalesce(sell_channel, 'Unknown')              as sell_channel,
        cast(quantity as integer)                      as quantity,
        initcap(category)                              as category,
        cast(timestamp as timestamp)                   as sold_at
    from source
)

select * from renamed;

