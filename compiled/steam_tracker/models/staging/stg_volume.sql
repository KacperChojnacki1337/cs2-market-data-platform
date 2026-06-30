select
    item_id,
    cast(volume_7d as int64) as volume_7d,
    timestamp as fetched_at
from `steam-tracker-portfolio`.`steam_raw`.`volume_history`