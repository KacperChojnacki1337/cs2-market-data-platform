
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select sell_tier
from `steam-tracker-portfolio`.`steam_marts`.`worth_to_sell`
where sell_tier is null



  
  
      
    ) dbt_internal_test