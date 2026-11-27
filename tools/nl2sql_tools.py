"""nl2sql_query：把问数（WenShu）LangGraph 全图封装为单个工具（计划书 3.1 核心融合点）。

- 问题进、结构化结果出；召回-生成SQL-校验-修正的复杂度封在图内部
- 返回结构化失败信号（status/missing/top_candidates），供 db 子智能体执行三层降级

跨线程修复：工具改为 async，让 3 次预算的 ContextVar 在事件循环内可见；
问数图是同步阻塞调用（内部另起 loop），用 asyncio.to_thread 包一层避免卡事件循环。
"""
import asyncio
import json

from langchain_core.tools import tool
from loguru import logger

from api.monitor import monitor
from tools.db_fallback import attempts_left, max_attempts, record_attempt, record_low_confidence
from tools.wenshu_runner import run_wenshu


@tool
async def nl2sql_query(question: str) -> str:
    """企业业务数据查询（NL2SQL）。自然语言问题进，返回结构化结果：
    - status=ok：data 为 CSV（首行列名），附带生成的 sql 供核查
    - status=low_confidence：召回置信度低，返回 missing（未命中的指标/表）与
      top_candidates（候选表），请按三层降级流程处理
    - status=failed：执行失败，error 说明原因
    :param question: 完整的、尽量明确的业务问题（含时间范围/口径/维度）
    """
    monitor.report_tool(tool_name="NL2SQL查询：nl2sql_query", args={"question": question[:120]})

    # 代码级预算：3 次用尽后直接拒绝，避免模型反复重试（提示词约束不可靠）
    if attempts_left() <= 0:
        return json.dumps(
            {
                "status": "attempts_exhausted",
                "attempts_used": f"{max_attempts()}/{max_attempts()}",
                "message": (
                    f"已达本会话 NL2SQL 尝试上限（{max_attempts()} 次），请停止重试。"
                    "立即调用 fallback_preview（表名可留空，会自动用最近一次召回的候选表）"
                    "取样例数据，并向用户提出澄清问题。"
                ),
            },
            ensure_ascii=False,
        )

    try:
        # 问数图同步阻塞（内部 run_coroutine_threadsafe + fut.result），丢线程池运行
        state = await asyncio.to_thread(run_wenshu, question)
    except TimeoutError as e:
        # 超时单独处理：旧逻辑把它归为"基础设施故障 + 可原样重试"，模型照做后
        # 3 次预算全耗在同一超时上（实测每次约 147s）。超时明确不可重试。
        return json.dumps(
            {
                "status": "timeout",
                "error": f"查询引擎超时：{e}",
                "retryable": False,
                "hint": (
                    "超时不要原样重试（每次约 2 分钟）。请改用 execute_sql_query 手写 SQL，"
                    "或调用 fallback_preview 保底并如实标注未采集。"
                ),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        # 基础设施类故障不消耗尝试预算（否则一次 ES/Qdrant 抖动就会把 3 次额度吃光）
        logger.exception(f"[nl2sql] 问数图执行异常：{e}")
        return json.dumps(
            {
                "status": "failed",
                "error": f"查询引擎异常：{type(e).__name__}: {e}",
                "retryable": True,
                "hint": "这是引擎/基础设施故障（非问题表述问题），未消耗尝试次数。"
                "可原样重试一次；若连续失败请改用 execute_sql_query 或告知用户数据源暂不可用。",
            },
            ensure_ascii=False,
        )

    attempt_no = record_attempt()  # 图正常跑完才计次（低置信度也计，因为确实消耗了一次召回）

    sql = state.get("sql") or ""
    rows = state.get("query_result") or []
    error = state.get("error")
    tables = state.get("table_infos") or []
    metrics = state.get("metric_infos") or []

    if error:
        return json.dumps(
            {"status": "failed", "error": error, "sql": sql, "attempt": f"{attempt_no}/{max_attempts()}"},
            ensure_ascii=False,
        )

    # 置信度判定：没有命中任何表/指标，或 SQL 为空，或结果为空 → low_confidence
    missing = []
    if not tables:
        missing.append("表：未在元数据库召回任何候选表")
    if not metrics:
        missing.append("指标：未在指标库命中标准指标（若问题涉及指标请改写表述）")
    if not sql and not rows:
        missing.append("SQL：未能生成有效SQL")

    if missing:
        candidates = [t.get("name", "") for t in tables[:3]]
        record_low_confidence(candidates, missing, question)  # 供 fallback_preview 自动选表
        exhausted = attempts_left() <= 0
        return json.dumps(
            {
                "status": "low_confidence",
                "attempt": f"{attempt_no}/{max_attempts()}",
                "missing": missing,
                "top_candidates": candidates,
                "hint": (
                    "尝试次数已用尽：直接调用 fallback_preview（表名可留空）取样例数据并向用户澄清。"
                    if exhausted
                    else "请按三层降级流程处理：先获取真实schema改写重试；简单查询可手写SQL；"
                    f"仅剩 {attempts_left()} 次机会，用尽后必须改用 fallback_preview 保底并向用户澄清。"
                ),
            },
            ensure_ascii=False,
        )

    header = ",".join(rows[0].keys()) if rows else ""
    lines = [",".join(map(str, r.values())) for r in rows[:100]]
    return json.dumps(
        {
            "status": "ok",
            "attempt": f"{attempt_no}/{max_attempts()}",
            "sql": sql,
            "row_count": len(rows),
            "data": "\n".join([header] + lines) or "(无数据)",
        },
        ensure_ascii=False,
    )
