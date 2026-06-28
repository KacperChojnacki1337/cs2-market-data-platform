

  create or replace view `steam-tracker-portfolio`.`steam_staging`.`int_latest_skinport_prices`
  OPTIONS()
  as with prices as (
    select * from `steam-tracker-portfolio`.`steam_staging`.`stg_skinport_prices`
),

latest as (
    select
        item_id,
        skinport_price_pln,
        fetched_at,
        row_number() over (
            partition by item_id
            order by fetched_at desc
        ) as rn
    from prices
)

select
    item_id,
    skinport_price_pln,
    fetched_at as skinport_price_fetched_at
from latest
where rn = 1;

