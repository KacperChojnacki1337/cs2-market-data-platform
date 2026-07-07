
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select past_peak
from `steam-tracker-portfolio`.`steam_marts`.`worth_to_sell`
where past_peak is null



  
  
      
    ) dbt_internal_test