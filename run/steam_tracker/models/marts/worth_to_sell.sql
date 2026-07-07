
  
    

    create or replace table `steam-tracker-portfolio`.`steam_marts`.`worth_to_sell`
      
    
    

    
    OPTIONS()
    as (
      -- worth_to_sell — held positions that have "done their job" and are worth cashing out.
--
-- Filters (hard):
--   A. net Skinport profit % >= 25   (item has realised a meaningful gain)
--   B. net Skinport profit  >= 50 PLN (worth the effort/fees — drops the penny stickers)
--
-- Liquidity and momentum are flags, NOT filters: an expensive but illiquid winner
-- (e.g. a knife) still surfaces, tagged TAKE PROFIT (illiquid) rather than hidden.
--
-- Value is measured in realisable cash (Skinport gross × 0.92, after the 8% fee),
-- not Steam gross — "spieniężyć" means real money, and the Steam wallet is closed.




with portfolio as (
    select * from `steam-tracker-portfolio`.`steam_marts`.`fct_portfolio`
),

-- Per-item 30-day price peak, for a "past peak" momentum flag.
-- NOTE: price history is currently ~1 month, so this signal is weak for now and
-- strengthens as history accumulates. Informational only (not a filter).
price_momentum as (
    select
        item_id,
        max(price_usd) as peak_price_usd_30d
    from `steam-tracker-portfolio`.`steam_staging`.`stg_prices`
    where not coalesce(price_flagged, false)
      and date(fetched_at) >= date_sub(current_date(), interval 30 day)
    group by item_id
),

final as (
    select
        p.asset_sk,
        p.item_id,
        p.category,
        p.quantity,
        p.buy_price_pln,

        -- Realisable cash (the "how much you actually get" number)
        p.net_value_skinport_pln,                       -- Skinport, after 8% fee
        p.real_cash_value_pln,                          -- CSFloat alternative (coeff)
        p.net_skinport_pnl_pln              as net_profit_pln,
        p.net_skinport_pnl_pct              as net_profit_pct,

        -- Liquidity (flag, not filter)
        p.liquidity_risk,
        p.volume_7d,

        -- Momentum: how far the current price sits below its 30-day peak
        p.current_price_usd,
        pm.peak_price_usd_30d,
        round((1 - safe_divide(p.current_price_usd, pm.peak_price_usd_30d)) * 100, 1)
                                            as pct_below_peak_30d,
        coalesce(p.current_price_usd <= 0.9 * pm.peak_price_usd_30d, false)
                                            as past_peak,

        -- Sell recommendation. Everything here already passed the profit filters,
        -- so the tier is just a liquidity split — no % cutoff (which would wrongly
        -- demote big-PLN low-% winners like a knife below cheap high-% drops).
        case
            when p.liquidity_risk = 'LOW' then 'TAKE PROFIT (illiquid)'
            else 'STRONG SELL'
        end                                 as sell_tier

    from portfolio p
    left join price_momentum pm on p.item_id = pm.item_id
    -- Free drops (buy_price = 0) have infinite ROI, so net_profit_pct is NULL and
    -- would fail `>= 25`. Treat zero cost as automatically passing the % test —
    -- a free drop worth >= 50 PLN is the purest sell candidate there is.
    where (p.net_skinport_pnl_pct >= 25 or p.buy_price_pln = 0)
      and p.net_skinport_pnl_pln >= 50
)

select * from final
order by net_profit_pln desc
    );
  