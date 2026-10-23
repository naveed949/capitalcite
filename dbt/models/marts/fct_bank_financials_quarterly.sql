{{ config(materialized="table") }}

with long_rows as (
    select * from {{ ref("int_bank_financials_long") }}
),

spine as (
    select distinct
        bank_key,
        quarter_id
    from long_rows
)

select
    spine.bank_key || '|' || cast(spine.quarter_id as varchar) as grain_key,
    spine.bank_key,
    spine.quarter_id,
    banks.ticker,
    banks.entity_level,
    max(case when long_rows.concept_key = 'us-gaap.Assets|USD' then long_rows.amount end) as assets_usd,
    max(case when long_rows.concept_key = 'us-gaap.Deposits|USD' then long_rows.amount end) as deposits_usd,
    max(case when long_rows.concept_key = 'us-gaap.NetIncomeLoss|USD' then long_rows.amount end) as net_income_quarter_usd,
    max(case when long_rows.concept_key = 'us-gaap.Assets|USD' then long_rows.accession end) as assets_sec_accn,
    max(case when long_rows.concept_key = 'us-gaap.Deposits|USD' then long_rows.accession end) as deposits_sec_accn,
    max(case when long_rows.concept_key = 'us-gaap.NetIncomeLoss|USD' then long_rows.accession end) as net_income_sec_accn,
    max(case when long_rows.concept_key = 'fdic.ASSET' then long_rows.amount end) as assets_usd_thousands,
    max(case when long_rows.concept_key = 'fdic.DEP' then long_rows.amount end) as deposits_usd_thousands,
    max(case when long_rows.concept_key = 'fdic.NETINC' then long_rows.amount end) as net_income_ytd_usd_thousands,
    max(case when long_rows.concept_key = 'fdic.NETINC_QUARTER' then long_rows.amount end) as net_income_quarter_usd_thousands,
    max(case when long_rows.concept_key = 'fdic.NIMY' then long_rows.amount end) as nimy_percent,
    max(case when long_rows.concept_key = 'fdic.EEFFR' then long_rows.amount end) as eeffr_percent,
    max(case when long_rows.concept_key like 'fdic.%' then long_rows.repdte end) as fdic_repdte,
    max(case when long_rows.concept_key like 'fdic.%' then long_rows.source_url end) as fdic_source_url
from spine
inner join {{ ref("dim_bank") }} as banks
    on spine.bank_key = banks.bank_key
left join long_rows
    on spine.bank_key = long_rows.bank_key
    and spine.quarter_id = long_rows.quarter_id
group by
    spine.bank_key,
    spine.quarter_id,
    banks.ticker,
    banks.entity_level
