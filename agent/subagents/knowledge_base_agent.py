from agent.prompts import sub_agents_content
from tools.ragflow_tools import get_assistant_list, create_ask_delete

knowledge_base_agent = {
    "name": sub_agents_content["kb"]["name"],
    "description": sub_agents_content["kb"]["description"],
    "system_prompt": sub_agents_content["kb"]["system_prompt"],
    "tools": [get_assistant_list, create_ask_delete],
}
