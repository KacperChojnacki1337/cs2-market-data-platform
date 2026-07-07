
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select steam_net_pln
from `steam-tracker-portfolio`.`steam_marts`.`rpt_portfolio_summary`
where steam_net_pln is null



  
  
      
    ) dbt_internal_test