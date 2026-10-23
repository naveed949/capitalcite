{{ config(materialized="table") }}

select
    unnest(
        generate_series(date '1980-01-01', date '2035-12-31', interval '1 day')
    ) as date_day
