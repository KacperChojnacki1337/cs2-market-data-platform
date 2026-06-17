
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select current_price_usd
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio`
where current_price_usd is null



  
  
      
    ) dbt_internal_test