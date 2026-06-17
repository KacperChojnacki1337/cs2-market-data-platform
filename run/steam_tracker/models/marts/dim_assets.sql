
  
    

    create or replace table `steam-tracker-portfolio`.`steam_marts`.`dim_assets`
      
    
    

    
    OPTIONS()
    as (
      with assets as (
    select * from `steam-tracker-portfolio`.`steam_staging`.`stg_assets`
),

-- Deduplicate: keep the latest record per asset_id
deduped as (
    select
        *,
        row_number() over (
            partition by asset_id
            order by last_updated desc
        ) as rn
    from assets
),

final as (
    select
        to_hex(md5(cast(coalesce(cast(asset_id as string), '_dbt_utils_surrogate_key_null_') as string))) as asset_sk,
        asset_id,
        item_id,
        buy_date,
        buy_price,
        buy_currency,
        quantity,
        category,
        purchase_channel,
        last_updated
    from deduped
    where rn = 1
)

select * from final
    );
  