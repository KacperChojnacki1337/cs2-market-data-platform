
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select real_cash_value_pln
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio`
where real_cash_value_pln is null



  
  
      
    ) dbt_internal_test