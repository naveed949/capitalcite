{{ config(enabled=target.name == "ci") }}

select
    grain_key,
    deposits_usd,
    net_income_quarter_usd,
    deposits_sec_accn,
    net_income_sec_accn
from {{ ref("fct_bank_financials_quarterly") }}
where
    bank_key = 'JPM|holding_company'
    and quarter_id = date '2026-06-30'
    and (
        deposits_usd is distinct from 2500000000000
        or net_income_quarter_usd is distinct from 13000000000
        or deposits_sec_accn is distinct from '0001628280-26-054343'
        or net_income_sec_accn is distinct from '0001628280-26-054343'
    )
