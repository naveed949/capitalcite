{{ config(materialized="table") }}

select
    {{ bank_key("ticker", "'holding_company'") }} as bank_key,
    ticker,
    'holding_company' as entity_level,
    cik,
    cast(null as varchar) as cert,
    cast(null as varchar) as lead_bank_name,
    source_note
from {{ ref("int_bank_entity_map") }}

union all

select
    {{ bank_key("ticker", "'bank_subsidiary'") }} as bank_key,
    ticker,
    'bank_subsidiary' as entity_level,
    cast(null as varchar) as cik,
    cert,
    lead_bank_name,
    source_note
from {{ ref("int_bank_entity_map") }}
