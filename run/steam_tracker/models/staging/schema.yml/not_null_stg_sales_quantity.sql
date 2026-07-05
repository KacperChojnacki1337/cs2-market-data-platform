
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select quantity
from `steam-tracker-portfolio`.`steam_staging`.`stg_sales`
where quantity is null



  
  
      
    ) dbt_internal_test