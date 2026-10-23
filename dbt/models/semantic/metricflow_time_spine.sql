{{ config(materialized="table") }}

select cast(generate_series as date) as date_day
from generate_series(date '1984-01-01', date '2030-12-31', interval 1 day)
