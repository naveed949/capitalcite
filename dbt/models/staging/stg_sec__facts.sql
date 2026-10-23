{{ config(materialized="table") }}

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
        columns = {cik: 'JSON', entityName: 'VARCHAR', facts: 'JSON'},
        maximum_object_size = 67108864
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

wanted as (
    select
        payloads.ticker,
        payloads.cik,
        payloads.entity_name,
        'us-gaap' as taxonomy,
        'Assets' as concept,
        'USD' as unit,
        json_extract(payloads.facts, '$."us-gaap".Assets.units.USD') as observations
    from payloads
    union all
    select
        payloads.ticker,
        payloads.cik,
        payloads.entity_name,
        'us-gaap',
        'Deposits',
        'USD',
        json_extract(payloads.facts, '$."us-gaap".Deposits.units.USD')
    from payloads
    union all
    select
        payloads.ticker,
        payloads.cik,
        payloads.entity_name,
        'us-gaap',
        'NetIncomeLoss',
        'USD',
        json_extract(payloads.facts, '$."us-gaap".NetIncomeLoss.units.USD')
    from payloads
),

unnested as (
    select
        wanted.ticker,
        wanted.cik,
        wanted.entity_name,
        wanted.taxonomy,
        wanted.concept,
        wanted.unit,
        try_cast(json_extract_string(obs.value, '$.start') as date) as period_start,
        try_cast(json_extract_string(obs.value, '$.end') as date) as period_end,
        try_cast(json_extract(obs.value, '$.fy') as integer) as fy,
        json_extract_string(obs.value, '$.fp') as fiscal_period,
        json_extract_string(obs.value, '$.form') as form,
        json_extract_string(obs.value, '$.accn') as accession,
        try_cast(json_extract_string(obs.value, '$.filed') as date) as filed,
        try_cast(json_extract(obs.value, '$.val') as double) as val
    from (
        select *
        from wanted
        where observations is not null
          and json_type(observations) = 'ARRAY'
    ) as wanted,
        json_each(wanted.observations) as obs
)

select
    unnested.cik
    || '|' || unnested.concept
    || '|' || unnested.unit
    || '|' || coalesce(cast(unnested.period_start as varchar), '')
    || '|' || cast(unnested.period_end as varchar)
    || '|' || coalesce(unnested.fiscal_period, '')
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
