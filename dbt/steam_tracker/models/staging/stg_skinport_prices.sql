with source as (
    select * from {{ source('steam_raw', 'skinport_prices_history') }}
),

renamed as (
    select
        item_id,
        cast(skinport_price_pln as numeric) as skinport_price_pln,
        cast(timestamp as timestamp)         as fetched_at
    from source
)

select * from renamed