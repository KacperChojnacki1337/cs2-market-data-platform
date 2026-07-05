
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

with all_values as (

    select
        buy_currency as value_field,
        count(*) as n_records

    from `steam-tracker-portfolio`.`steam_staging`.`stg_assets`
    group by buy_currency

)

select *
from all_values
where value_field not in (
    'PLN'
)



  
  
      
    ) dbt_internal_test