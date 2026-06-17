
    
    

with dbt_test__target as (

  select asset_sk as unique_field
  from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio`
  where asset_sk is not null

)

select
    unique_field,
    count(*) as n_records

from dbt_test__target
group by unique_field
having count(*) > 1


