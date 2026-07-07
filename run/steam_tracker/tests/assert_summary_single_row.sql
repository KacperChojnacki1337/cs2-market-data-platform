
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  -- rpt_portfolio_summary must always be exactly one row (it is a single-grain
-- metrics layer). Fails if the aggregation ever produces zero or multiple rows.

select count(*) as row_count
from `steam-tracker-portfolio`.`steam_marts`.`rpt_portfolio_summary`
having count(*) <> 1
  
  
      
    ) dbt_internal_test