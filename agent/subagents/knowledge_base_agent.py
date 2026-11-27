from langchain.agents.middleware import ToolCallLimitMiddleware

from agent.prompts import sub_agents_content
from api.monitor import StuckNudgeMiddleware
from tools.evidence_tools import save_evidence
from tools.ragflow_tools import get_assistant_list, create_ask_delete, search_knowledge_base

# 硬限流：知识库检索每轮最多 16 次，超限自动短路并引导模型收尾（省钱、防空转）。
_TOOL_RUN_LIMIT = 16

knowledge_base_agent = {
    "name": sub_agents_content["kb"]["name"],
    "description": sub_agents_content["kb"]["description"],
    "system_prompt": sub_agents_content["kb"]["system_prompt"] + "\n" + sub_agents_content["kb"].get("evidence_note", ""),
    # search_knowledge_base 必须注册：它是知识库层主路径（直连 retrieval API，
    # 不依赖助手列表/绑定）。缺了它时助手通道 403 会导致整层证据归零。
    "tools": [search_knowledge_base, get_assistant_list, create_ask_delete, save_evidence],
    "middleware": [
        ToolCallLimitMiddleware(tool_name="search_knowledge_base", run_limit=_TOOL_RUN_LIMIT, exit_behavior="continue"),
        StuckNudgeMiddleware(),
    ],
}
