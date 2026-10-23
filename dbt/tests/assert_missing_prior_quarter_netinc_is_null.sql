{{ config(enabled=target.name == "ci") }}

select 'missing gap row' as reason, cast(null as varchar) as grain_key
where not exists (
    select 1
    from {{ ref("fct_bank_financials_quarterly") }}
    where
        bank_key = 'JPM|bank_subsidiary'
        and quarter_id = date '2099-09-30'
)

union all

select
    'quarterly value is not null' as reason,
    grain_key
from {{ ref("fct_bank_financials_quarterly") }}
where
    bank_key = 'JPM|bank_subsidiary'
    and quarter_id = date '2099-09-30'
    and (
        net_income_quarter_usd_thousands is not null
        or net_income_ytd_usd_thousands is distinct from 300000
    )
