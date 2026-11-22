"""研判 agent：读三层证据 → 交叉验证 + 可信度打分 + 动态权重 → 产出研判报告。

只在主智能体判定用户问题为"深度分析"模式时派发。
证据来自 tools/evidence_tools.py 落盘的 evidence/{layer}/findings.md。
"""
from agent.prompts import sub_agents_content
from tools.evidence_tools import read_evidence
from tools.markdown_tools import generate_markdown

analysis_agent = {
    "name": sub_agents_content["analysis"]["name"],
    "description": sub_agents_content["analysis"]["description"],
    "system_prompt": sub_agents_content["analysis"]["system_prompt"],
    "tools": [read_evidence, generate_markdown],
}
