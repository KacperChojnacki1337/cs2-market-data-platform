with source as (
    select * from {{ source('steam_raw', 'sales_history') }}
),

renamed as (
    select
        asset_id,
        item_id,
        cast(sell_date as date)      as sell_date,
        cast(sell_price as numeric)  as sell_price,
        upper(sell_currency)         as sell_currency,
        cast(quantity as integer)    as quantity,
        initcap(category)            as category,
        purchase_channel,
        cast(timestamp as timestamp) as sold_at
    from source
)

select * from renamed