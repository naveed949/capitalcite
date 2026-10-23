{% macro snapshot_glob(kind) -%}
{%- if target.name == "dev" -%}
    {%- set paths = {
        "xbrl_payload": "data/raw/xbrl/*.json",
        "xbrl_sidecar": "data/raw/xbrl/*.metadata.json",
        "fdic_payload": "data/raw/fdic/financials/*.json",
        "fdic_sidecar": "data/raw/fdic/financials/*.metadata.json",
    } -%}
{%- elif target.name == "ci" -%}
    {%- set paths = {
        "xbrl_payload": "tests/fixtures/metrics/xbrl/*.json",
        "xbrl_sidecar": "tests/fixtures/metrics/xbrl/*.metadata.json",
        "fdic_payload": "tests/fixtures/metrics/fdic/*.json",
        "fdic_sidecar": "tests/fixtures/metrics/fdic/*.metadata.json",
    } -%}
{%- else -%}
    {{ exceptions.raise_compiler_error("unknown dbt target " ~ target.name) }}
{%- endif -%}
{%- if kind not in paths -%}
    {{ exceptions.raise_compiler_error("unknown snapshot kind " ~ kind) }}
{%- endif -%}
{{- paths[kind] -}}
{%- endmacro %}
