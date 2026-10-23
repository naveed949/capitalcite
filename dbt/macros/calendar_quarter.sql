{% macro calendar_quarter_end(date_expr) %}
(
    case
        when {{ date_expr }} is null then null
        when month({{ date_expr }}) in (3, 6, 9, 12)
            and {{ date_expr }} = last_day({{ date_expr }})
        then cast({{ date_expr }} as date)
        else null
    end
)
{% endmacro %}

{% macro calendar_quarter_start(date_expr) %}
(
    case month({{ date_expr }})
        when 3 then make_date(year({{ date_expr }}), 1, 1)
        when 6 then make_date(year({{ date_expr }}), 4, 1)
        when 9 then make_date(year({{ date_expr }}), 7, 1)
        when 12 then make_date(year({{ date_expr }}), 10, 1)
        else null
    end
)
{% endmacro %}
