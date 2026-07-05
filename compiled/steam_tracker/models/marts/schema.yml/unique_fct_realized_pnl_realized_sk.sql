
    
    

with dbt_test__target as (

  select realized_sk as unique_field
  from `steam-tracker-portfolio`.`steam_marts`.`fct_realized_pnl`
  where realized_sk is not null

)

select
    unique_field,
    count(*) as n_records

from dbt_test__target
group by unique_field
having count(*) > 1


