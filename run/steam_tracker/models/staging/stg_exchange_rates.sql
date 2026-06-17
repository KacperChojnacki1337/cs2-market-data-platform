

  create or replace view `steam-tracker-portfolio`.`steam_staging`.`stg_exchange_rates`
  OPTIONS()
  as with source as (
    select * from `steam-tracker-portfolio`.`steam_raw`.`exchange_rates`
),

renamed as (
    select
        from_currency,
        to_currency,
        cast(rate as numeric)        as rate,
        source                       as rate_source,
        cast(timestamp as timestamp) as fetched_at
    from source
)

select * from renamed;

