from agent.prompts import sub_agents_content
from tools.evidence_tools import save_evidence
from tools.tavily_tool import internet_search

network_search_agent = {
    "name": sub_agents_content["search"]["name"],
    "description": sub_agents_content["search"]["description"],
    "system_prompt": sub_agents_content["search"]["system_prompt"] + "\n" + sub_agents_content["search"].get("evidence_note", ""),
    "tools": [internet_search, save_evidence],
}
