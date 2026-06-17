
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select realized_pnl_pln
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where realized_pnl_pln is null



  
  
      
    ) dbt_internal_test