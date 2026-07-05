-- FIFO unit-level matching between buy lots and sales.
--
-- Problem: a single item_id can be bought in several lots (different asset_id,
-- date, price) and a single sale can span multiple lots. Linking buys to sales
-- by item_id alone double-counts multi-lot items and cannot split a sale across
-- lots. This model resolves it with First-In-First-Out (FIFO) accounting.
--
-- Mechanism:
--   1. Explode every buy lot into individual units (quantity -> N rows).
--   2. Number those units globally per item, ordered by buy_date (FIFO:
--      oldest lot consumed first). Tie-break same-day lots by asset_id for
--      determinism.
--   3. Explode every sale into units and number them per item by sell_date.
--   4. Match buy unit #N to sell unit #N. A buy unit with a matching sell unit
--      is sold; without one it is still held.
--
-- Grain: one row per bought unit. Downstream:
--   - fct_realized_pnl aggregates sold units by (sale x buy lot).
--   - fct_portfolio counts held units per buy lot (remaining quantity).
--   - The oversell test flags sale units with no matching buy unit.

with buys as (
    select * from {{ ref('dim_assets') }}
),

sales as (
    select * from {{ ref('stg_sales') }}
),

-- One row per bought unit, FIFO-ordered per item
buy_units as (
    select
        b.asset_id,
        b.item_id,
        b.buy_date,
        b.buy_price,
        b.buy_currency,
        b.category,
        b.purchase_channel,
        row_number() over (
            partition by b.item_id
            order by b.buy_date asc, b.asset_id asc, unit_offset asc
        ) as unit_seq
    from buys b,
    unnest(generate_array(1, b.quantity)) as unit_offset
),

-- One row per sold unit, chronologically ordered per item
sell_units as (
    select
        s.asset_id as sell_id,
        s.item_id,
        s.sell_date,
        s.sell_price,
        s.sell_channel,
        s.sold_at,
        row_number() over (
            partition by s.item_id
            order by s.sell_date asc, s.asset_id asc, unit_offset asc
        ) as unit_seq
    from sales s,
    unnest(generate_array(1, s.quantity)) as unit_offset
),

-- Match buy unit #N to sell unit #N within each item
matched as (
    select
        bu.item_id,
        bu.unit_seq,
        bu.asset_id                    as buy_asset_id,
        bu.buy_date,
        bu.buy_price,
        bu.buy_currency,
        bu.category,
        bu.purchase_channel,
        su.sell_id,
        su.sell_date,
        su.sell_price,
        su.sell_channel,
        su.sold_at,
        su.sell_id is not null         as is_sold
    from buy_units bu
    left join sell_units su
        on  bu.item_id  = su.item_id
        and bu.unit_seq = su.unit_seq
)

select * from matched