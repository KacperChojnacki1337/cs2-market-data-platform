
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select units_sold_from_lot
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where units_sold_from_lot is null



  
  
      
    ) dbt_internal_test