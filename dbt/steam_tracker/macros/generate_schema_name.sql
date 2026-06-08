{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set suffix = '_dev' if target.name == 'dev' else '' -%}
    {%- if custom_schema_name == 'staging' -%}
        steam_staging{{ suffix }}
    {%- elif custom_schema_name == 'marts' -%}
        steam_marts{{ suffix }}
    {%- else -%}
        {{ target.schema }}
    {%- endif -%}
{%- endmacro %}
