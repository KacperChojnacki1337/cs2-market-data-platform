
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select net_value_steam_pln
from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio`
where net_value_steam_pln is null



  
  
      
    ) dbt_internal_test