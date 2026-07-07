
    
    

with all_values as (

    select
        sell_tier as value_field,
        count(*) as n_records

    from `steam-tracker-portfolio`.`steam_marts`.`worth_to_sell`
    group by sell_tier

)

select *
from all_values
where value_field not in (
    'STRONG SELL','TAKE PROFIT (illiquid)'
)


