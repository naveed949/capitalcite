select
    bank_key,
    quarter_id,
    concept_key,
    count(*) as n
from {{ ref("int_bank_financials_long") }}
group by bank_key, quarter_id, concept_key
having count(*) > 1
