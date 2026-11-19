"""数据库保底预览工具（决策1 第 3 层，专门 function 而非提示词约定）。

为什么单独成函数：
- 保底 SQL 若交给模型拼，既可能拼错，也可能被误导成 ``select * from 别的表``；
- 这里由代码渲染（配置模板 ``nl2sql.fallback_preview_sql``），表名走白名单校验，
  且**默认自动复用** nl2sql 最近一次 low_confidence 召回得分最高的候选表；
- 返回结构化结果 + 澄清话术，主智能体据此向用户追问，不再无谓重试。

配套的代码级预算：
- ``record_attempt()`` 由 nl2sql_query 调用计次，用尽后第 4 次调用被硬拦截；
- ``record_low_confidence()`` 缓存候选表，供本工具自动选表。
"""
import json
import re
from contextvars import ContextVar

from langchain_core.tools import tool

from api.monitor import monitor
from core.config import cfg
from tools.db_tools import _run_query

# 会话级状态（一次请求一份，由 server 在任务开始处 reset）
_db_state: ContextVar[dict | None] = ContextVar("db_fallback_state", default=None)

# 合法标识符：table 或 db.table；字母数字下划线 $（不含空格/分号/注释符）
_IDENT = re.compile(r"^[0-9A-Za-z_$]+(\.[0-9A-Za-z_$]+)?$")
# 系统库不允许预览
_FORBIDDEN_PREFIX = ("information_schema", "mysql.", "performance_schema", "sys.")


def _state() -> dict:
    st = _db_state.get()
    if st is None:  # 未显式 reset 时自建，保证计数生效
        st = {"attempts": 0, "candidates": [], "missing": [], "last_question": ""}
        _db_state.set(st)
    return st


def reset_db_budget():
    """每次会话请求开始时调用：清空尝试计数与候选表缓存。"""
    _db_state.set({"attempts": 0, "candidates": [], "missing": [], "last_question": ""})


def max_attempts() -> int:
    return int(cfg["nl2sql"]["max_attempts"])


def record_attempt() -> int:
    """nl2sql_query 每次调用记一次，返回已用次数。"""
    st = _state()
    st["attempts"] += 1
    return st["attempts"]


def attempts_left() -> int:
    return max(0, max_attempts() - _state()["attempts"])


def record_low_confidence(candidates: list[str], missing: list[str], question: str = ""):
    """低置信度时缓存候选表与缺失项，供保底工具自动选表。"""
    st = _state()
    st["candidates"] = [c for c in candidates if c]
    st["missing"] = list(missing)
    st["last_question"] = question


def _sanitize(table_name: str) -> str:
    """去反引号/空白，校验标识符与系统库；不合法返回空串。"""
    t = (table_name or "").strip().strip("`").strip()
    if not t or not _IDENT.match(t):
        return ""
    if t.lower().startswith(_FORBIDDEN_PREFIX):
        return ""
    return t


@tool
def fallback_preview(table_name: str = "") -> str:
    """保底数据预览（三层降级的最后一层）。nl2sql_query 连续尝试未拿到可信结果时调用本工具。

    不要自己手写 `select * from ...` 语句——表名与 LIMIT 由本工具统一处理。
    返回：候选表、10 行数据预览、以及需要向用户澄清的问题清单。

    :param table_name: 表名；留空则自动使用 nl2sql 最近一次召回得分最高的候选表
    """
    st = _state()
    picked = _sanitize(table_name)
    auto_picked = False
    if not picked:
        for cand in st["candidates"]:
            picked = _sanitize(cand)
            if picked:
                auto_picked = True
                break

    monitor.report_tool(
        tool_name="数据库保底预览：fallback_preview",
        args={"table": picked or "(无候选)", "auto": auto_picked, "attempts": f"{st['attempts']}/{max_attempts()}"},
    )

    if not picked:
        return json.dumps(
            {
                "status": "no_candidate",
                "candidates": st["candidates"],
                "message": (
                    "没有可用的候选表（nl2sql 未召回任何表，或传入表名不合法）。"
                    "请先调用 list_sql_tables 确认表名，或直接向用户澄清：需要查询哪类业务数据。"
                ),
                "need_clarification": True,
                "clarify_questions": ["要分析的业务对象是什么？（如订单/商品/客户）", "时间范围是什么？", "需要对比哪些维度？"],
            },
            ensure_ascii=False,
        )

    sql = cfg["nl2sql"]["fallback_preview_sql"].format(table_name=picked)
    raw = _run_query(sql, limit=10)

    return json.dumps(
        {
            "status": "need_clarification",
            "table": picked,
            "auto_selected": auto_picked,
            "sql": sql,
            "preview": raw,
            "candidates": st["candidates"],
            "missing": st["missing"],
            "attempts_used": f"{st['attempts']}/{max_attempts()}",
            "need_clarification": True,
            "message": (
                f"已尽力尝试（{st['attempts']}/{max_attempts()} 次）仍未得到可靠查询结果，"
                f"先返回表 {picked} 的样例数据供用户确认。"
                "请把预览数据与候选表一并呈现给用户，并询问：可以再描述得清楚一点吗？"
                "（如时间范围、指标口径、涉及的业务线）"
            ),
            "clarify_questions": [
                "时间范围是什么？（如 2026 年 Q3、最近 30 天）",
                "指标口径是什么？（如销售额是否含税/含退款）",
                "涉及哪条业务线或哪类商品？",
            ],
        },
        ensure_ascii=False,
    )
