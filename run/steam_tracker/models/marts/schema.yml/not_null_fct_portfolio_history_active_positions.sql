
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select active_positions
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio_history`
where active_positions is null



  
  
      
    ) dbt_internal_test