
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select snapshot_date
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio_history`
where snapshot_date is null



  
  
      
    ) dbt_internal_test