
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select sell_price_pln
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where sell_price_pln is null



  
  
      
    ) dbt_internal_test