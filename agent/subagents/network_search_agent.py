from agent.prompts import sub_agents_content
from api.monitor import StuckNudgeMiddleware
from tools.evidence_tools import save_evidence
from tools.tavily_tool import internet_search
from tools.vlm_tools import analyze_product_image

network_search_agent = {
    "name": sub_agents_content["search"]["name"],
    "description": sub_agents_content["search"]["description"],
    "system_prompt": sub_agents_content["search"]["system_prompt"] + "\n" + sub_agents_content["search"].get("evidence_note", ""),
    "tools": [internet_search, analyze_product_image, save_evidence],
    "middleware": [StuckNudgeMiddleware()],
}
