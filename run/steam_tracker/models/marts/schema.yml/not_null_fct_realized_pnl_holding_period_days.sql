
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select holding_period_days
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where holding_period_days is null



  
  
      
    ) dbt_internal_test