
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select net_profit_pln
from `steam-tracker-portfolio`.`steam_marts`.`worth_to_sell`
where net_profit_pln is null



  
  
      
    ) dbt_internal_test