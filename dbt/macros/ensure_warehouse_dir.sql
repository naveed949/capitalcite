{% macro ensure_warehouse_dir() %}
{%- if execute -%}
{%- set project_dir = env_var("DBT_PROJECT_DIR", "dbt") -%}
{%- do modules.os.makedirs(project_dir ~ "/../data/warehouse", exist_ok=True) -%}
{%- endif -%}
select 1 as warehouse_dir_ready
{% endmacro %}
