"""Tavily 网络搜索工具：会话级调用计数器硬拦截（计划书：搜索限制不能只靠提示词）。

跨线程修复：主 agent 走 astream + DeepAgents 的 atask，整条链路是 async。
工具若定义为同步函数，LangGraph 会把它丢进线程池执行，Python 3.12 以下
run_in_executor 不传播 ContextVar → 这里读到 None、计数从 0 重建 → 预算形同虚设。
所以工具改为 async def（在事件循环内执行，继承会话 ContextVar），
阻塞的 SDK / Redis 调用用 asyncio.to_thread 包一层（to_thread 内部 copy_context）。
"""
import asyncio
import json
from contextvars import ContextVar

from langchain_core.tools import tool
from tavily import TavilyClient

from api.monitor import monitor
from core.config import cfg

_search_state: ContextVar[dict | None] = ContextVar("search_state", default=None)

_client: TavilyClient | None = None

# 图片源黑名单：素材库/代发图站点，图与具体竞品无关，属"什么图都找"的低质来源
_IMG_BLOCK_HOSTS = (
    "stock.adobe", "shutterstock", "unsplash", "gettyimages", "istockphoto",
    "pexels", "pixabay", "freepik", "dreamstime", "vecteezy",
    "alicdn.com", "aliexpress", "dhgate", "made-in-china", "1688.com",
)
_MAX_IMAGES_PER_CALL = 2


def _filter_images(images: list) -> list[str]:
    """过滤图片直链：剔除素材库/代发图站点，去重，单次最多保留 2 张。"""
    out: list[str] = []
    for raw in images or []:
        url = raw.get("url") if isinstance(raw, dict) else raw
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        low = url.lower()
        if any(h in low for h in _IMG_BLOCK_HOSTS):
            continue
        if url in out:
            continue
        out.append(url)
        if len(out) >= _MAX_IMAGES_PER_CALL:
            break
    return out


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
async def internet_search(query: str, search_depth: str = "advanced", include_images: bool = False) -> str:
    """互联网公开信息检索。

    :param query: 检索关键词（一次聚焦一个信息维度）。取竞品主图时 query 必须带品牌名，
                  如"富光 保温杯 商品主图 详情页"，禁止泛词（vacuum bottle photo 会返回素材库图）。
    :param search_depth: basic 或 advanced（默认 advanced）
    :param include_images: 是否同时采集图片直链（每次最多返回 2 张，已过滤掉素材库/代发图站点）
    :return: JSON 字符串：results[].{title,url,content}；include_images 时附 images[]；超限返回拦截提示
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

    cache_key = make_key("tavily", query, search_depth, f"img={include_images}")
    cached = await asyncio.to_thread(get_json, cache_key)
    if cached:
        cached["cached"] = True
        return json.dumps(cached, ensure_ascii=False)

    try:
        # 阻塞 SDK 调用丢线程池；to_thread 内部 copy_context，主流程的会话状态不丢
        resp = await asyncio.to_thread(
            _get_client().search,
            query=query,
            search_depth=search_depth,
            max_results=int(cfg["search"]["max_results_per_call"]),
            include_images=include_images,
        )
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in resp.get("results", [])
        ]
        payload = {"blocked": False, "call": f"{state['calls']}/{max_calls}", "results": results}
        if include_images:
            # 只留可用直链（已滤掉素材库/代发图），每次最多 2 张；调用方须自行绑定来源品牌
            payload["images"] = _filter_images(resp.get("images") or [])
        await asyncio.to_thread(set_json, cache_key, payload, int(cfg["redis"]["ttl"]["tavily"]))
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:  # Tavily 限流/异常 → 明确报错而非静默
        return json.dumps({"blocked": False, "error": f"搜索服务异常（可能是限流，请稍后整合已有信息）: {e}"}, ensure_ascii=False)
