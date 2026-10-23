with bank_years as (
    select
        facts.bank_key,
        periods.calendar_year,
        count(*) as quarters,
        count(facts.net_income_quarter_usd_thousands) as quarterly_n,
        max(
            case
                when periods.calendar_quarter = 4
                then facts.net_income_ytd_usd_thousands
            end
        ) as q4_ytd,
        sum(facts.net_income_quarter_usd_thousands) as quarter_sum
    from {{ ref("fct_bank_financials_quarterly") }} as facts
    inner join {{ ref("dim_period") }} as periods
        on facts.quarter_id = periods.quarter_id
    where facts.entity_level = 'bank_subsidiary'
    group by facts.bank_key, periods.calendar_year
)

select
    bank_key,
    calendar_year,
    quarters,
    quarterly_n,
    q4_ytd,
    quarter_sum
from bank_years
where
    quarters = 4
    and quarterly_n = 4
    and q4_ytd is not null
    and abs(quarter_sum - q4_ytd) > 1
