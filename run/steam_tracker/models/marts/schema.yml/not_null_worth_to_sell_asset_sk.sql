
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select asset_sk
from `steam-tracker-portfolio`.`steam_marts`.`worth_to_sell`
where asset_sk is null



  
  
      
    ) dbt_internal_test