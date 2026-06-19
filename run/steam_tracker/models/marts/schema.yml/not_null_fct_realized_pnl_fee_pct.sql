
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select fee_pct
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where fee_pct is null



  
  
      
    ) dbt_internal_test