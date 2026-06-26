
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select real_cash_coeff
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio`
where real_cash_coeff is null



  
  
      
    ) dbt_internal_test