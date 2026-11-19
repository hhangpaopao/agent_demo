"""Tavily 网络搜索工具：会话级调用计数器硬拦截（计划书：搜索限制不能只靠提示词）。"""
import json
from contextvars import ContextVar

from langchain_core.tools import tool
from tavily import TavilyClient

from api.monitor import monitor
from core.config import cfg

_search_state: ContextVar[dict | None] = ContextVar("search_state", default=None)

_client: TavilyClient | None = None


def _get_client() -> TavilyClient:
    global _client
    if _client is None:
        # tavily-python 自动读取环境变量 TAVILY_API_KEY
        _client = TavilyClient()
    return _client


def reset_search_budget():
    """每次会话请求开始时调用，重置该会话的搜索计数。"""
    _search_state.set({"calls": 0})


@tool
def internet_search(query: str, search_depth: str = "advanced") -> str:
    """互联网公开信息检索。

    :param query: 检索关键词（一次聚焦一个信息维度）
    :param search_depth: basic 或 advanced（默认 advanced）
    :return: JSON 字符串：results[].{title,url,content}；超过次数上限时返回拦截提示
    """
    max_calls = int(cfg["search"]["max_calls"])
    state = _search_state.get()
    if state is None:  # 兜底：未显式重置时也保证计数生效
        state = {"calls": 0}
        _search_state.set(state)

    if state["calls"] >= max_calls:
        return json.dumps(
            {"blocked": True, "message": f"已达本会话检索上限（{max_calls}次），请基于已有信息综合，不要继续搜索。"},
            ensure_ascii=False,
        )
    state["calls"] += 1

    monitor.report_tool(tool_name="网络搜索：internet_search", args={"query": query, "call": f"{state['calls']}/{max_calls}"})

    # 缓存命中直接返回（相同关键词 10 分钟内不重复调用付费 API）
    from core.cache import get_json, set_json, make_key

    cache_key = make_key("tavily", query, search_depth)
    cached = get_json(cache_key)
    if cached:
        cached["cached"] = True
        return json.dumps(cached, ensure_ascii=False)

    try:
        resp = _get_client().search(query=query, search_depth=search_depth, max_results=int(cfg["search"]["max_results_per_call"]))
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in resp.get("results", [])
        ]
        payload = {"blocked": False, "call": f"{state['calls']}/{max_calls}", "results": results}
        set_json(cache_key, payload, int(cfg["redis"]["ttl"]["tavily"]))
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:  # Tavily 限流/异常 → 明确报错而非静默
        return json.dumps({"blocked": False, "error": f"搜索服务异常（可能是限流，请稍后整合已有信息）: {e}"}, ensure_ascii=False)
