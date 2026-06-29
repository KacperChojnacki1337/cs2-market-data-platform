with ranked as (
    select
        item_id,
        volume_7d,
        fetched_at,
        row_number() over (partition by item_id order by fetched_at desc) as rn
    from {{ ref('stg_volume') }}
)

select
    item_id,
    volume_7d,
    fetched_at
from ranked
where rn = 1