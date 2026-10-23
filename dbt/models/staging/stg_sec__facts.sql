{{ config(materialized="view") }}

with payloads as (
    select
        filename,
        regexp_extract(filename, '([^/\\]+)\.json$', 1) as ticker,
        lpad(trim(both '"' from cast(cik as varchar)), 10, '0') as cik,
        entityName as entity_name,
        facts
    from read_json(
        '{{ snapshot_glob("xbrl_payload") }}',
        filename = true,
        columns = {cik: 'JSON', entityName: 'VARCHAR', facts: 'JSON'}
    )
    where filename not like '%.metadata.json'
),

sidecars as (
    select
        regexp_extract(filename, '([^/\\]+)\.metadata\.json$', 1) as ticker,
        url as source_url,
        fetched_at
    from read_json(
        '{{ snapshot_glob("xbrl_sidecar") }}',
        filename = true,
        columns = {url: 'VARCHAR', fetched_at: 'VARCHAR', sha256: 'VARCHAR', bytes: 'BIGINT'}
    )
),

unnested as (
    select
        payloads.ticker,
        payloads.cik,
        payloads.entity_name,
        tax.key as taxonomy,
        concept.key as concept,
        unit.key as unit,
        try_cast(json_extract_string(obs.value, '$.start') as date) as period_start,
        try_cast(json_extract_string(obs.value, '$.end') as date) as period_end,
        try_cast(json_extract(obs.value, '$.fy') as integer) as fy,
        json_extract_string(obs.value, '$.fp') as fiscal_period,
        json_extract_string(obs.value, '$.form') as form,
        json_extract_string(obs.value, '$.accn') as accession,
        try_cast(json_extract_string(obs.value, '$.filed') as date) as filed,
        try_cast(json_extract(obs.value, '$.val') as double) as val
    from payloads,
        json_each(payloads.facts) as tax,
        json_each(tax.value) as concept,
        json_each(json_extract(concept.value, '$.units')) as unit,
        json_each(unit.value) as obs
)

select
    unnested.cik
    || '|' || unnested.concept
    || '|' || unnested.unit
    || '|' || cast(unnested.period_end as varchar)
    || '|' || unnested.fiscal_period
    || '|' || unnested.form
    || '|' || unnested.accession as grain_key,
    unnested.ticker,
    unnested.cik,
    unnested.entity_name,
    unnested.taxonomy,
    unnested.concept,
    unnested.unit,
    unnested.period_start,
    unnested.period_end,
    unnested.fy,
    unnested.fiscal_period,
    unnested.form,
    unnested.accession,
    unnested.filed,
    unnested.val,
    sidecars.source_url,
    sidecars.fetched_at
from unnested
inner join {{ ref("int_bank_entity_map") }} as entities
    on unnested.ticker = entities.ticker
    and unnested.cik = entities.cik
left join sidecars
    on unnested.ticker = sidecars.ticker
