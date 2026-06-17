

  create or replace view `steam-tracker-portfolio`.`steam_staging`.`stg_prices`
  OPTIONS()
  as with source as (
    select * from `steam-tracker-portfolio`.`steam_raw`.`prices_history`
),

renamed as (
    select
        item_id,
        cast(price_usd as numeric)   as price_usd,
        price_flagged,
        cast(timestamp as timestamp) as fetched_at
    from source
)

select * from renamed;

