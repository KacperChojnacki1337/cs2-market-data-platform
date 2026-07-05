
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select is_sold
from `steam-tracker-portfolio`.`steam_staging`.`int_fifo_units`
where is_sold is null



  
  
      
    ) dbt_internal_test