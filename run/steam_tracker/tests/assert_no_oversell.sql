
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  -- Oversell guard: fails if any item has more units sold than ever bought.
-- Under FIFO this would leave sale units with no matching buy unit, silently
-- dropping them from realized PnL and corrupting held quantities.

with bought as (
    select item_id, sum(quantity) as bought_qty
    from `steam-tracker-portfolio`.`steam_marts`.`dim_assets`
    group by item_id
),

sold as (
    select item_id, sum(quantity) as sold_qty
    from `steam-tracker-portfolio`.`steam_staging`.`stg_sales`
    group by item_id
)

select
    s.item_id,
    s.sold_qty,
    coalesce(b.bought_qty, 0) as bought_qty
from sold s
left join bought b using (item_id)
where s.sold_qty > coalesce(b.bought_qty, 0)
  
  
      
    ) dbt_internal_test