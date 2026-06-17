
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select usd_pln_rate
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio`
where usd_pln_rate is null



  
  
      
    ) dbt_internal_test