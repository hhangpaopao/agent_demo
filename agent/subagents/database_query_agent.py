from agent.prompts import sub_agents_content
from tools.db_fallback import fallback_preview
from tools.db_tools import list_sql_tables, get_table_data, execute_sql_query
from tools.nl2sql_tools import nl2sql_query

database_query_agent = {
    "name": sub_agents_content["db"]["name"],
    "description": sub_agents_content["db"]["description"],
    "system_prompt": sub_agents_content["db"]["system_prompt"],
    "tools": [nl2sql_query, list_sql_tables, get_table_data, execute_sql_query, fallback_preview],
}
