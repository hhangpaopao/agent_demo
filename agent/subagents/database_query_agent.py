from langchain.agents.middleware import ToolCallLimitMiddleware

from agent.prompts import sub_agents_content
from api.monitor import StuckNudgeMiddleware
from tools.db_fallback import fallback_preview
from tools.db_tools import list_sql_tables, get_table_data, execute_sql_query
from tools.evidence_tools import save_evidence
from tools.nl2sql_tools import nl2sql_query

# 硬限流：单个高频工具每轮最多 16 次，超限自动短路并引导模型收尾（省钱、防空转）。
# run_limit 用 UntrackedValue 按轮独立，不跨会话累积。
_TOOL_RUN_LIMIT = 16
_LIMIT_TOOLS = ("execute_sql_query", "nl2sql_query", "get_table_data")

database_query_agent = {
    "name": sub_agents_content["db"]["name"],
    "description": sub_agents_content["db"]["description"],
    "system_prompt": sub_agents_content["db"]["system_prompt"] + "\n" + sub_agents_content["db"].get("evidence_note", ""),
    "tools": [nl2sql_query, list_sql_tables, get_table_data, execute_sql_query, fallback_preview, save_evidence],
    "middleware": [
        ToolCallLimitMiddleware(tool_name=name, run_limit=_TOOL_RUN_LIMIT, exit_behavior="continue")
        for name in _LIMIT_TOOLS
    ] + [StuckNudgeMiddleware()],
}
