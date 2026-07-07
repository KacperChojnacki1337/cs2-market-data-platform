-- rpt_portfolio_summary must always be exactly one row (it is a single-grain
-- metrics layer). Fails if the aggregation ever produces zero or multiple rows.

select count(*) as row_count
from `steam-tracker-portfolio`.`steam_marts`.`rpt_portfolio_summary`
having count(*) <> 1