
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select portfolio_value_pln
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio_history`
where portfolio_value_pln is null



  
  
      
    ) dbt_internal_test