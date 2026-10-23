{{ config(materialized="table") }}

with ends as (
    select period_end
    from {{ ref("stg_sec__facts") }}
    union
    select period_end
    from {{ ref("stg_fdic__financials") }}
)

select
    period_end as quarter_id,
    year(period_end) as calendar_year,
    cast(month(period_end) as integer) // 3 as calendar_quarter
from ends
where {{ calendar_quarter_end("period_end") }} is not null
