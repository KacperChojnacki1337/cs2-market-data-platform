
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select positions
from `steam-tracker-portfolio`.`steam_marts`.`rpt_portfolio_summary`
where positions is null



  
  
      
    ) dbt_internal_test