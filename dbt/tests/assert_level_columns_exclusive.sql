select grain_key as id
from {{ ref("fct_bank_financials_quarterly") }}
where (
    assets_usd is not null
    or deposits_usd is not null
    or net_income_quarter_usd is not null
    or assets_sec_accn is not null
    or deposits_sec_accn is not null
    or net_income_sec_accn is not null
    or net_income_method is not null
    or net_income_nine_month_sec_accn is not null
)
and (
    assets_usd_thousands is not null
    or deposits_usd_thousands is not null
    or net_income_ytd_usd_thousands is not null
    or net_income_quarter_usd_thousands is not null
    or nimy_percent is not null
    or eeffr_percent is not null
    or fdic_repdte is not null
    or fdic_source_url is not null
)

union all

select bank_key as id
from {{ ref("dim_bank") }}
where (
    entity_level = 'holding_company'
    and (cik is null or cert is not null or lead_bank_name is not null)
)
or (
    entity_level = 'bank_subsidiary'
    and (cert is null or cik is not null)
)
