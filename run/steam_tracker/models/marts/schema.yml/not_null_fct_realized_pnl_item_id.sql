
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select item_id
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where item_id is null



  
  
      
    ) dbt_internal_test