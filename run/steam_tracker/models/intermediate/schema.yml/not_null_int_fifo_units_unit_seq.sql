
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select unit_seq
from `steam-tracker-portfolio`.`steam_staging`.`int_fifo_units`
where unit_seq is null



  
  
      
    ) dbt_internal_test