
    
    

with child as (
    select item_id as from_field
    from `steam-tracker-portfolio`.`steam_staging`.`stg_sales`
    where item_id is not null
),

parent as (
    select item_id as to_field
    from `steam-tracker-portfolio`.`steam_marts`.`dim_assets`
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null


