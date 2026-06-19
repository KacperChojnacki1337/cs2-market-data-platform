
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select sell_channel
from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
where sell_channel is null



  
  
      
    ) dbt_internal_test