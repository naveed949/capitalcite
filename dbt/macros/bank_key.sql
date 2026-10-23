{% macro bank_key(ticker_expr, level_expr) %}
({{ ticker_expr }} || '|' || {{ level_expr }})
{% endmacro %}
