{% macro repo_root() -%}
{%- set project_dir = env_var("DBT_PROJECT_DIR", "") -%}
{%- if project_dir -%}
{{- project_dir }}/..
{%- else -%}
.
{%- endif -%}
{%- endmacro %}

{% macro snapshot_glob(kind) -%}
{%- set root = repo_root() -%}
{%- if target.name == "dev" -%}
    {%- set paths = {
        "xbrl_payload": root ~ "/data/raw/xbrl/*.json",
        "xbrl_sidecar": root ~ "/data/raw/xbrl/*.metadata.json",
        "fdic_payload": root ~ "/data/raw/fdic/financials/*.json",
        "fdic_sidecar": root ~ "/data/raw/fdic/financials/*.metadata.json",
    } -%}
{%- elif target.name == "ci" -%}
    {%- set paths = {
        "xbrl_payload": root ~ "/tests/fixtures/metrics/xbrl/*.json",
        "xbrl_sidecar": root ~ "/tests/fixtures/metrics/xbrl/*.metadata.json",
        "fdic_payload": root ~ "/tests/fixtures/metrics/fdic/*.json",
        "fdic_sidecar": root ~ "/tests/fixtures/metrics/fdic/*.metadata.json",
    } -%}
{%- else -%}
    {{ exceptions.raise_compiler_error("unknown dbt target " ~ target.name) }}
{%- endif -%}
{%- if kind not in paths -%}
    {{ exceptions.raise_compiler_error("unknown snapshot kind " ~ kind) }}
{%- endif -%}
{{- paths[kind] -}}
{%- endmacro %}
