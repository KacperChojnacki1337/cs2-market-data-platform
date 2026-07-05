
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select item_id
from `steam-tracker-portfolio`.`steam_staging`.`stg_assets`
where item_id is null



  
  
      
    ) dbt_internal_test