{{ config(materialized="table") }}

with payloads as (
    select
        filename,
        regexp_extract(filename, '([^/\\]+)\.json$', 1) as file_stem,
        data
    from read_json(
        '{{ snapshot_glob("fdic_payload") }}',
        filename = true,
        columns = {data: 'JSON', meta: 'JSON'}
    )
    where filename not like '%.metadata.json'
),

sidecars as (
    select
        regexp_extract(filename, '([^/\\]+)\.metadata\.json$', 1) as cert,
        url as source_url,
        fetched_at
    from read_json(
        '{{ snapshot_glob("fdic_sidecar") }}',
        filename = true,
        columns = {url: 'VARCHAR', fetched_at: 'VARCHAR', sha256: 'VARCHAR', bytes: 'BIGINT'}
    )
),

unnested as (
    select
        payloads.file_stem,
        cast(try_cast(json_extract(row.value, '$.data.CERT') as integer) as varchar) as cert,
        json_extract_string(row.value, '$.data.REPDTE') as repdte,
        try_cast(strptime(json_extract_string(row.value, '$.data.REPDTE'), '%Y%m%d') as date) as period_end,
        try_cast(json_extract(row.value, '$.data.ASSET') as double) as asset,
        try_cast(json_extract(row.value, '$.data.DEP') as double) as dep,
        try_cast(json_extract(row.value, '$.data.NETINC') as double) as netinc,
        try_cast(json_extract(row.value, '$.data.NIMY') as double) as nimy,
        try_cast(json_extract(row.value, '$.data.EEFFR') as double) as eeffr
    from payloads,
        json_each(payloads.data) as row
)

select
    unnested.cert || '|' || unnested.repdte as grain_key,
    unnested.cert,
    unnested.repdte,
    unnested.period_end,
    unnested.asset,
    unnested.dep,
    unnested.netinc,
    unnested.nimy,
    unnested.eeffr,
    sidecars.source_url,
    sidecars.fetched_at
from unnested
inner join {{ ref("int_bank_entity_map") }} as entities
    on unnested.cert = entities.cert
    and unnested.file_stem = entities.cert
left join sidecars
    on unnested.cert = sidecars.cert
