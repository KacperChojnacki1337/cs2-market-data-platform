
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  





with validation_errors as (

    select
        item_id, unit_seq
    from `steam-tracker-portfolio`.`steam_staging`.`int_fifo_units`
    group by item_id, unit_seq
    having count(*) > 1

)

select *
from validation_errors



  
  
      
    ) dbt_internal_test