"""核验 agent：拿原始证据（raw.md）逐条挑研判报告的刺。

借鉴 MultiAgent-Research-Analyst 的 Critique 设计：
查三样——缺口（gaps）、覆盖度（coverage）、无依据断言（unsupported claims），
硬纪律：必须引用 raw.md 的具体片段才能判"无依据"，禁止凭模型感觉。
输出结构化问题清单，由主智能体决定是否回炉重做（最多 2 轮）。
"""
from agent.prompts import sub_agents_content
from tools.evidence_tools import read_evidence

verification_agent = {
    "name": sub_agents_content["verification"]["name"],
    "description": sub_agents_content["verification"]["description"],
    "system_prompt": sub_agents_content["verification"]["system_prompt"],
    "tools": [read_evidence],
}
