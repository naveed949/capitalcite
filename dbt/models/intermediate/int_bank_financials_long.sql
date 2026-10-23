{{ config(materialized="view") }}

with entities as (
    select * from {{ ref("int_bank_entity_map") }}
),

sec_ranked as (
    select
        {{ bank_key("entities.ticker", "'holding_company'") }} as bank_key,
        facts.period_end as quarter_id,
        facts.taxonomy || '.' || facts.concept || '|' || facts.unit as concept_key,
        facts.val as amount,
        'usd' as reported_unit,
        facts.accession,
        cast(null as varchar) as repdte,
        facts.source_url,
        facts.filed,
        facts.form,
        facts.period_start,
        facts.concept,
        row_number() over (
            partition by
                facts.cik,
                facts.taxonomy,
                facts.concept,
                facts.unit,
                facts.period_end,
                facts.period_start
            order by
                case
                    when facts.form in ('10-K', '10-K/A') then 0
                    when facts.form in ('10-Q', '10-Q/A') then 1
                    else 2
                end,
                facts.filed desc nulls last,
                facts.accession desc
        ) as rn
    from {{ ref("stg_sec__facts") }} as facts
    inner join entities
        on facts.ticker = entities.ticker
        and facts.cik = entities.cik
    where
        {{ calendar_quarter_end("facts.period_end") }} is not null
        and facts.taxonomy = 'us-gaap'
        and facts.unit = 'USD'
        and facts.concept in ('Assets', 'Deposits', 'NetIncomeLoss')
),

sec_latest as (
    select * from sec_ranked where rn = 1
),

sec_balances as (
    select
        bank_key,
        quarter_id,
        concept_key,
        amount,
        reported_unit,
        accession,
        repdte,
        source_url
    from sec_latest
    where concept in ('Assets', 'Deposits')
),

sec_three_month as (
    select
        bank_key,
        quarter_id,
        concept_key,
        amount,
        reported_unit,
        accession,
        repdte,
        source_url
    from sec_latest
    where
        concept = 'NetIncomeLoss'
        and period_start = {{ calendar_quarter_start("quarter_id") }}
),

sec_annual as (
    select *
    from sec_latest
    where
        concept = 'NetIncomeLoss'
        and period_start = make_date(year(quarter_id), 1, 1)
        and quarter_id = make_date(year(quarter_id), 12, 31)
),

sec_nine_month as (
    select *
    from sec_latest
    where
        concept = 'NetIncomeLoss'
        and period_start = make_date(year(quarter_id), 1, 1)
        and quarter_id = make_date(year(quarter_id), 9, 30)
),

sec_q4_derived as (
    select
        annual.bank_key,
        annual.quarter_id,
        annual.concept_key,
        annual.amount - nine.amount as amount,
        annual.reported_unit,
        annual.accession,
        annual.repdte,
        annual.source_url
    from sec_annual as annual
    inner join sec_nine_month as nine
        on annual.bank_key = nine.bank_key
        and year(annual.quarter_id) = year(nine.quarter_id)
    where not exists (
        select 1
        from sec_three_month as three
        where
            three.bank_key = annual.bank_key
            and three.quarter_id = annual.quarter_id
    )
),

sec_long as (
    select * from sec_balances
    union all
    select * from sec_three_month
    union all
    select * from sec_q4_derived
),

fdic_observed as (
    select
        {{ bank_key("entities.ticker", "'bank_subsidiary'") }} as bank_key,
        financials.period_end as quarter_id,
        financials.repdte,
        financials.source_url,
        financials.asset,
        financials.dep,
        financials.netinc,
        financials.nimy,
        financials.eeffr,
        cast(month(financials.period_end) as integer) // 3 as calendar_quarter,
        lag(financials.netinc) over (
            partition by financials.cert, year(financials.period_end)
            order by financials.period_end
        ) as prior_ytd,
        cast(month(lag(financials.period_end) over (
            partition by financials.cert, year(financials.period_end)
            order by financials.period_end
        )) as integer) // 3 as prior_quarter
    from {{ ref("stg_fdic__financials") }} as financials
    inner join entities
        on financials.cert = entities.cert
    where {{ calendar_quarter_end("financials.period_end") }} is not null
),

fdic_with_quarter as (
    select
        bank_key,
        quarter_id,
        repdte,
        source_url,
        asset,
        dep,
        netinc,
        nimy,
        eeffr,
        case
            when calendar_quarter = 1 then netinc
            when prior_quarter = calendar_quarter - 1 then netinc - prior_ytd
            else null
        end as netinc_quarter
    from fdic_observed
),

fdic_long as (
    select bank_key, quarter_id, 'fdic.ASSET' as concept_key, asset as amount, 'thousands_usd' as reported_unit, cast(null as varchar) as accession, repdte, source_url from fdic_with_quarter
    union all
    select bank_key, quarter_id, 'fdic.DEP', dep, 'thousands_usd', null, repdte, source_url from fdic_with_quarter
    union all
    select bank_key, quarter_id, 'fdic.NETINC', netinc, 'thousands_usd', null, repdte, source_url from fdic_with_quarter
    union all
    select bank_key, quarter_id, 'fdic.NETINC_QUARTER', netinc_quarter, 'thousands_usd', null, repdte, source_url from fdic_with_quarter
    union all
    select bank_key, quarter_id, 'fdic.NIMY', nimy, 'percent', null, repdte, source_url from fdic_with_quarter
    union all
    select bank_key, quarter_id, 'fdic.EEFFR', eeffr, 'percent', null, repdte, source_url from fdic_with_quarter
)

select
    bank_key || '|' || cast(quarter_id as varchar) || '|' || concept_key as grain_key,
    bank_key,
    quarter_id,
    concept_key,
    amount,
    reported_unit,
    accession,
    repdte,
    source_url
from (
    select * from sec_long
    union all
    select * from fdic_long
) as combined
