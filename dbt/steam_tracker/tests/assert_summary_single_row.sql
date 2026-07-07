-- rpt_portfolio_summary must always be exactly one row (it is a single-grain
-- metrics layer). Fails if the aggregation ever produces zero or multiple rows.

select count(*) as row_count
from {{ ref('rpt_portfolio_summary') }}
having count(*) <> 1