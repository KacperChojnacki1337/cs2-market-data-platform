

  create or replace view `steam-tracker-portfolio`.`steam_staging`.`int_latest_prices`
  OPTIONS()
  as with prices as (
    select * from `steam-tracker-portfolio`.`steam_staging`.`stg_prices`
),

valid_prices as (
    select * from prices
    where not coalesce(price_flagged, false)
),

latest as (
    select
        item_id,
        price_usd,
        fetched_at,
        row_number() over (
            partition by item_id
            order by fetched_at desc
        ) as rn
    from valid_prices
)

select
    item_id,
    price_usd,
    fetched_at as price_fetched_at
from latest
where rn = 1;

