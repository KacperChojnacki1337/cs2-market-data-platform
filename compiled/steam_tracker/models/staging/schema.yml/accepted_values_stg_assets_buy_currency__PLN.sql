
    
    

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


