"""子智能体元数据装配：**描述与名字留在这里，提示词正文从 `prompts/*.md` 加载**。

分工：
- `prompts/{name}.md`  → 只有文案（改措辞不用动代码，非工程同学也能改）
- `core/load_prompt.py`→ 加载、缓存、`${var}` 渲染
- 本文件              → 名字 / description / 绑定的工具集（结构化数据，留在代码里便于审查）

新增一个子智能体三步：① prompts/ 加 md；② load_prompt.ALIASES 登记短名；
③ 本文件加一段元数据 + 在对应 subagents/*.py 里绑定工具。

注意：变量用 `${var}`（string.Template 语法），不要用 `{var}`——
提示词里本来就有 SQL 模板 `{table_name}` 这类花括号，`str.format` 会炸。
"""
from core.config import cfg  # 顺带完成 .env 加载，保证下面的数值来自统一配置
from core.load_prompt import load_prompt

main_agent_content = {
    "system_prompt": load_prompt("main"),
}

sub_agents_content = {
    "search": {
        "name": "network_search_agent",
        "description": "网络搜索助手：负责公开知识广域检索，由浅入深、多轮递进搜索，覆盖多个信息维度",
        "system_prompt": load_prompt("search", max_calls=cfg["search"]["max_calls"]),
    },
    "db": {
        "name": "database_query_agent",
        "description": "数据库查询助手：对接企业业务数据库，通过NL2SQL引擎提取商品/业务明细数据，低置信度时具备三层降级能力",
        "system_prompt": load_prompt("db", max_attempts=cfg["nl2sql"]["max_attempts"]),
    },
    "kb": {
        "name": "knowledge_base_agent",
        "description": "RAGFlow知识库助手：查询企业内部知识库与当前会话用户上传资料，分层提问、深度检索",
        "system_prompt": load_prompt("kb"),
    },
}
