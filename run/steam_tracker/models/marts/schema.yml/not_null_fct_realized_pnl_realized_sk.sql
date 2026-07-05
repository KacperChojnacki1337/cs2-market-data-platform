
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select realized_sk
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where realized_sk is null



  
  
      
    ) dbt_internal_test