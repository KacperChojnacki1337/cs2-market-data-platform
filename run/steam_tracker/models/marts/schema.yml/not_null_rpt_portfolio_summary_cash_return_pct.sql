
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select cash_return_pct
from `steam-tracker-portfolio`.`steam_marts`.`rpt_portfolio_summary`
where cash_return_pct is null



  
  
      
    ) dbt_internal_test