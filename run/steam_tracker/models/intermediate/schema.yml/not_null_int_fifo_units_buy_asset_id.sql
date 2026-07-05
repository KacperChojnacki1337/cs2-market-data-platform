
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select buy_asset_id
from `steam-tracker-portfolio`.`steam_staging`.`int_fifo_units`
where buy_asset_id is null



  
  
      
    ) dbt_internal_test