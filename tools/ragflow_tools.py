"""RAGFlow 知识库工具：内部助手（共享只读） + 用户上传库（会话级/账号级，计划书 3.3 混合方案）。

跨线程修复：LLM 工具改为 async，在事件循环内读取会话 ID ContextVar；
阻塞的 RAGFlow SDK 调用用 asyncio.to_thread 包一层。
"""
import asyncio
import json
import time
from datetime import datetime, timedelta

from langchain_core.tools import tool
from ragflow_sdk import RAGFlow

from api.monitor import monitor
from core.config import cfg

_client: RAGFlow | None = None


def get_client() -> RAGFlow:
    global _client
    if _client is None:
        _client = RAGFlow(api_key=cfg["ragflow"]["api_key"], base_url=cfg["ragflow"]["base_url"])
    return _client


def _prefix() -> str:
    return cfg["ragflow"]["user_dataset_prefix"]


def _retrieval(question: str, dataset_ids: list[str]) -> list[dict]:
    """RAGFlow 检索接口（显式传混合检索配比 + rerank）。

    使用 HTTP API 而非 SDK，因为需要 vector_similarity_weight / rerank_id 这些
    SDK 未暴露的参数：
    - vector_similarity_weight：向量 vs 关键词(BM25) 的混合配比
    - rerank_id：粗排召回后的 cross-encoder 精排模型
    """
    import httpx

    rcfg = cfg["ragflow"]["retrieval"]

    # Redis 缓存：相同问题+数据集 10 分钟内复用检索结果
    from core.cache import get_json, set_json, make_key

    cache_key = make_key("rag", question, ",".join(sorted(dataset_ids)), str(rcfg.get("rerank_id", "")))
    cached = get_json(cache_key)
    if cached is not None:
        return cached

    payload = {
        "question": question,
        "dataset_ids": dataset_ids,
        "page": 1,
        "page_size": int(rcfg["top_k"]),
        "top_k": int(rcfg["top_k"]) * 16,  # 粗排池：给 rerank 留足够候选
        "similarity_threshold": float(rcfg["similarity_threshold"]),
        "vector_similarity_weight": float(rcfg["vector_similarity_weight"]),
        "keyword": False,
        "highlight": False,
    }
    if rcfg.get("rerank_id"):
        payload["rerank_id"] = rcfg["rerank_id"]

    url = f"{cfg['ragflow']['base_url'].rstrip('/')}/api/v1/retrieval"
    headers = {"Authorization": f"Bearer {cfg['ragflow']['api_key']}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if data.get("code") not in (0, None):
            raise RuntimeError(data.get("message", "retrieval 返回非0 code"))
        chunks = (data.get("data") or {}).get("chunks") or []
        result = [
            {
                "content": c.get("content", ""),
                "similarity": c.get("similarity"),
                "vector_similarity": c.get("vector_similarity"),
                "term_similarity": c.get("term_similarity"),
                "document": (c.get("document_keyword") or c.get("docnm_kwd") or ""),
            }
            for c in chunks
        ]
        if result:  # 空结果不缓存，避免"暂时没搜到"被固化 10 分钟
            set_json(cache_key, result, int(cfg["redis"]["ttl"]["ragflow"]))
        return result
    except Exception as e:
        # 降级：HTTP 失败退回 SDK（无混合配比/精排参数，但保证可用）
        from loguru import logger

        logger.warning(f"[ragflow] HTTP retrieval 失败，降级 SDK：{e}")
        cli = get_client()
        res = cli.retrieval(question=question, dataset_ids=dataset_ids, top_k=int(rcfg["top_k"]))
        chunks = res.get("chunks", []) if isinstance(res, dict) else res
        return [{"content": getattr(c, "content", str(c)), "similarity": None} for c in chunks]


# ---------- 用户 dataset 管理（供 API 层与入库管线调用，不是 LLM 工具） ----------

def ensure_session_dataset(session_id: str) -> str:
    """确保会话级 dataset 存在，返回 dataset id。"""
    name = f"{_prefix()}{session_id}"
    for ds in get_client().list_datasets(name=name):
        return ds.id
    ds = get_client().create_dataset(name=name, description=f"会话 {session_id} 用户上传资料（TTL {cfg['ragflow']['user_dataset_ttl_hours']}h）")
    return ds.id


def upload_md_to_session_dataset(session_id: str, md_path, display_name: str | None = None) -> str:
    """上传 md 文件到会话 dataset（入库管线终点）。"""
    ds_id = ensure_session_dataset(session_id)
    datasets = get_client().list_datasets(id=ds_id)
    if not datasets:
        raise RuntimeError(f"dataset {ds_id} 不存在")
    with open(md_path, "rb") as f:
        docs = datasets[0].upload_documents([{ "display_name": display_name or f"{md_path}", "blob": f.read() }])
    return json.dumps({"ok": True, "dataset_id": ds_id, "documents": [d.get("id", "") for d in docs] if isinstance(docs, list) else str(docs)}, ensure_ascii=False)


def ensure_knowledge_dataset(name: str) -> str:
    """长期共享知识库：名字不带 user_upload_ 前缀，不进 TTL 清理。

    走 HTTP 而非 SDK：云端 RAGFlow 的 SDK create_dataset 返回的对象上传时会报
    "lacks permission for dataset"（与 _list_datasets_http 同源的 SDK 兼容问题）。
    """
    import httpx

    base = cfg["ragflow"]["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {cfg['ragflow']['api_key']}"}
    with httpx.Client(timeout=30) as client:
        for ds in _list_datasets_http():
            if ds.get("name") == name:
                return ds["id"]
        resp = client.post(f"{base}/api/v1/datasets", headers={**headers, "Content-Type": "application/json"},
                           json={"name": name, "description": "长期品类知识库"})
        body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"建库失败：{body}")
    return body["data"]["id"]


def upload_md_to_knowledge_dataset(name: str, md_path, display_name: str | None = None) -> str:
    """上传品类 md 到长期知识库并触发解析（检索端 dataset_name 留空即可命中）。

    两步都走 HTTP：上传 POST /datasets/{id}/documents（multipart），
    解析 POST /datasets/{id}/chunks。同名文档已存在则跳过，避免重复。
    """
    import httpx
    from pathlib import Path

    base = cfg["ragflow"]["base_url"].rstrip("/")
    headers = {"Authorization": f"Bearer {cfg['ragflow']['api_key']}"}
    ds_id = ensure_knowledge_dataset(name)
    display = display_name or Path(md_path).name
    with httpx.Client(timeout=60) as client:
        listing = client.get(f"{base}/api/v1/datasets/{ds_id}/documents", headers=headers,
                             params={"page": 1, "page_size": 100}).json()
        docs = (listing.get("data") or {}).get("docs") or []
        if any(d.get("name") == display for d in docs):
            return json.dumps({"ok": True, "skipped": True, "dataset_id": ds_id, "reason": "同名文档已存在"}, ensure_ascii=False)
        with open(md_path, "rb") as f:
            up = client.post(f"{base}/api/v1/datasets/{ds_id}/documents", headers=headers,
                             files={"file": (display, f, "text/markdown")}).json()
        if up.get("code") != 0:
            raise RuntimeError(f"上传失败：{up}")
        doc_id = (up.get("data") or [{}])[0].get("id")
        if doc_id:
            client.post(f"{base}/api/v1/datasets/{ds_id}/chunks",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"document_ids": [doc_id]})
    return json.dumps({"ok": True, "dataset_id": ds_id, "documents": [doc_id] if doc_id else []}, ensure_ascii=False)


def cleanup_expired_datasets() -> list[str]:
    """TTL 惰性清理：删除超过时限无活动的会话级 dataset（决策2）。后台定时任务调用。"""
    ttl_h = int(cfg["ragflow"]["user_dataset_ttl_hours"])
    cutoff = time.time() - ttl_h * 3600
    removed = []
    for ds in get_client().list_datasets():
        if not ds.name.startswith(_prefix()):
            continue
        session_id = ds.name[len(_prefix()):]
        try:
            mtime = _latest_activity(session_id)
            if mtime < cutoff:
                get_client().delete_datasets(ids=[ds.id])
                removed.append(ds.name)
        except Exception as e:  # 清理失败不影响主流程
            print(f"清理 dataset {ds.name} 失败: {e}")
    return removed


def _latest_activity(session_id: str) -> float:
    """会话最近活动时间：优先取 output/session_x 目录 mtime，缺失则按 dataset 创建时间处理。"""
    from core.config import PROJECT_ROOT, cfg as _cfg

    d = PROJECT_ROOT / _cfg["paths"]["output_dir"] / f"session_{session_id}"
    if d.exists():
        return d.stat().st_mtime
    return time.time()  # 无目录信息时视为活跃，宁可晚删不可误删


# ---------- LLM 工具 ----------

@tool
async def get_assistant_list() -> str:
    """查询 RAGFlow 中可用的助手与知识库（含内部库与当前会话的用户上传资料库）。
    向某个助手提问前，必须先调用本工具获取助手名称。"""
    monitor.report_tool(tool_name="知识库助手列表：get_assistant_list", args={})
    # sid 在事件循环内读取（ContextVar），SDK 阻塞调用丢线程池
    from core.context import get_session_id

    sid = get_session_id()
    try:
        return await asyncio.to_thread(_list_assistants_sync, sid)
    except Exception as e:
        return f"查询助手信息异常：{e}"


def _list_chats_http() -> list:
    """列举助手，绕开 SDK list_chats 的解析 bug。

    ragflow_sdk 0.x 的 list_chats 写死 res["data"]["chats"]，但云端 RAGFlow
    新版返回 data 直接是数组，字符串索引数组会抛
    "list indices must be integers or slices, not str"。
    这里直接走 HTTP 并兼容两种返回结构（data 为 list 或 {"chats": [...]}）。
    """
    import httpx

    url = cfg["ragflow"]["base_url"].rstrip("/") + "/api/v1/chats"
    headers = {"Authorization": f"Bearer {cfg['ragflow']['api_key']}"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=headers, params={"page": 1, "page_size": 100})
        resp.raise_for_status()
        data = resp.json().get("data") or []
    return data if isinstance(data, list) else (data.get("chats") or [])


def _list_datasets_http() -> list[dict]:
    """列举知识库，绕开 SDK list_datasets 的空响应解析 bug（Expecting value）。

    与 _list_chats_http 同理走 HTTP；不依赖服务端 name 过滤参数，拉全量后本地过滤。
    """
    import httpx

    url = cfg["ragflow"]["base_url"].rstrip("/") + "/api/v1/datasets"
    headers = {"Authorization": f"Bearer {cfg['ragflow']['api_key']}"}
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers=headers, params={"page": 1, "page_size": 100})
        resp.raise_for_status()
        data = resp.json().get("data") or []
    if isinstance(data, dict):
        data = data.get("datasets") or []
    return data


def _chat_name(chat) -> str:
    return (chat.get("name") if isinstance(chat, dict) else getattr(chat, "name", "")) or ""


def _chat_id(chat) -> str:
    return (chat.get("id") if isinstance(chat, dict) else getattr(chat, "id", "")) or ""


def _chat_ds_names(chat) -> list[str]:
    """兼容 dict / SDK 对象，且关联知识库可能在 datasets 或 dataset_ids。"""
    raw = chat.get("datasets") if isinstance(chat, dict) else getattr(chat, "datasets", None)
    if not raw and not isinstance(chat, dict):
        raw = getattr(chat, "dataset_ids", None)
    names = []
    for ds in (raw or []):
        if isinstance(ds, dict):
            value = ds.get("name") or ds.get("id")
        else:
            value = getattr(ds, "name", None) or getattr(ds, "id", None)
        if value:
            names.append(str(value))
    return names


def _list_assistants_sync(sid: str) -> str:
    """同步列举助手 + 会话资料库，由 get_assistant_list 经 to_thread 调用。

    通道故障（403/超时）时必须显式报错，不能返回空列表——否则上层 agent 会
    拿不到助手名、转而按字面瞎猜（如"高端产品线档案"），导致整层证据归零。
    """
    try:
        chats = _list_chats_http()
    except Exception as e:
        return (
            f"【知识库通道故障】无法获取助手列表：{type(e).__name__}: {e}。"
            "本次 document 层不可用。禁止猜测助手名称、禁止用臆测的名字调用 create_ask_delete；"
            "请在报告中标注'知识库层通道故障，未采集'，不要臆造该层内容。"
        )
    lines = []
    for chat in chats:
        ds_names = _chat_ds_names(chat)
        desc = chat.get("description") if isinstance(chat, dict) else getattr(chat, "description", "")
        lines.append(f"[内部库] 助手名称:{_chat_name(chat)};功能介绍:{desc};关联知识库:{'、'.join(ds_names)}")
    if sid:
        try:
            user_ds = [d for d in _list_datasets_http() if d.get("name") == f"{_prefix()}{sid}"]
            if user_ds:
                lines.append(f"[会话资料库] 名称:{user_ds[0]['name']}（用户本会话上传的资料，可直接检索）")
        except Exception as e:
            lines.append(f"[会话资料库] 列举失败（不影响内部库）：{type(e).__name__}: {e}")
    return "\n".join(lines) if lines else "没有任何可用助手"


# 会话级助手提问计数：防止 agent 撞上 403/空命中后换着问法无限重试。
# 与 search_budget / db_budget 同思路，每轮 chat 开始时由 server 重置。
_ASK_CALL_LIMIT = 8
_ask_call_count: dict[str, int] = {}


def reset_ask_budget():
    """每轮 chat 开始时重置助手提问计数。"""
    _ask_call_count.clear()


@tool
async def create_ask_delete(chat_name: str, question: str) -> str:
    """向指定助手创建单次会话提问，问完即删。
    内部助手直接传 chat_name；若 chat_name 传"会话资料库"则对当前会话用户上传资料做深度检索。
    :param chat_name: get_assistant_list 返回的助手名称
    :param question: 提问内容
    """
    monitor.report_tool(tool_name="知识库提问：create_ask_delete", args={"chat_name": chat_name, "question": question[:120]})
    # sid 在事件循环内读取（ContextVar），阻塞 SDK 调用丢线程池
    from core.context import get_session_id

    sid = get_session_id()
    key = sid or "__global__"
    # 计数在事件循环内完成（无 await 打断），多协程并发安全。
    # 超限直接返回硬错误，不给 agent"换个问法再试"的余地。
    count = _ask_call_count.get(key, 0) + 1
    _ask_call_count[key] = count
    if count > _ASK_CALL_LIMIT:
        monitor.report_error(f"知识库助手提问熔断：本轮已调用 {count - 1} 次，超过上限 {_ASK_CALL_LIMIT}")
        return (
            f"【已熔断】本轮助手提问已调用 {count - 1} 次，超过上限 {_ASK_CALL_LIMIT} 次。"
            "禁止再以任何问法调用本工具。请立即改用 search_knowledge_base 直接检索知识库原文；"
            "若已检索仍然无果，如实标注'知识库未检索到'并结束本层采集，不要继续重试助手。"
        )
    try:
        return await asyncio.to_thread(_ask_sync, chat_name, question, sid)
    except Exception as e:
        return f"提问异常：{e}"


def _ask_sync(chat_name: str, question: str, sid: str) -> str:
    """同步提问（会话资料库检索 / 内部助手流式问答），由 create_ask_delete 经 to_thread 调用。"""
    cli = get_client()
    # 会话资料库 → 直接走 retrieval API（无需临时 chat）
    # dataset 列举走 HTTP，绕开 SDK list_datasets 空响应（Expecting value）。
    if sid and (chat_name == "会话资料库" or chat_name == f"{_prefix()}{sid}"):
        try:
            ds = [d for d in _list_datasets_http() if d.get("name") == f"{_prefix()}{sid}"]
        except Exception as e:
            return f"【知识库通道故障】无法列举会话资料库：{type(e).__name__}: {e}"
        if not ds:
            return "当前会话没有用户上传资料。"
        chunks = _retrieval(question, [ds[0]["id"]])
        if not chunks:
            return (
                "当前会话资料库暂未检索到相关内容。若你刚上传了文件，资料可能还在入库处理中"
                "（前端会显示“上传ing”，完成后再试）；也可换个说法或更具体的关键词。"
            )
        parts = []
        for i, c in enumerate(chunks, 1):
            score = c.get("similarity")
            score_str = f" 相似度{score:.2f}" if isinstance(score, (int, float)) else ""
            parts.append(f"[片段{i}{score_str}] {c['content']}")
        return "\n".join(parts)

    # 绕开 SDK list_chats 的解析 bug：用 HTTP 列表按名字匹配，
    # 再拿返回的原始 dict 构造 Chat 对象（构造函数不走 list_chats）。
    from ragflow_sdk.modules.chat import Chat

    try:
        chats = _list_chats_http()
    except Exception as e:
        return (
            f"【知识库通道故障】无法获取助手列表：{type(e).__name__}: {e}。"
            "本次提问未完成。禁止猜测助手名称重试；请标注'知识库层通道故障，未采集'。"
        )
    matches = [c for c in chats if _chat_name(c) == chat_name]
    if not matches:
        names = "、".join(_chat_name(c) for c in chats) or "（无）"
        return (
            f"助手「{chat_name}」不存在。当前可用助手只有：{names}。"
            "请使用 get_assistant_list 返回的准确名称，禁止按问题字面臆测助手名。"
        )
    # 同名助手取最新创建的：用户重建助手后旧的同名条目仍在，matches[0]
    # 可能指向旧的（prompt 缺 {knowledge} 的那个）。create_time/update_time
    # 为毫秒时间戳；字段缺失时保持服务端返回顺序，不影响原有行为。
    def _created(c):
        for k in ("create_time", "update_time"):
            v = c.get(k) if isinstance(c, dict) else getattr(c, k, None)
            if isinstance(v, (int, float)) and v > 0:
                return v
        return 0
    matches.sort(key=_created, reverse=True)
    chat = Chat(cli, matches[0])
    session = chat.create_session(name="temp_session_ask")
    try:
        # RAGFlow 流式片段是"从头累积的完整文本"而非增量，
        # 逐段 += 会把同一段话重复 N 次。取最后一段才是最终答案。
        import re

        result = ""
        for piece in session.ask(question=question, stream=True):
            content = piece.content if hasattr(piece, "content") else str(piece)
            if content:
                result = content  # 覆盖式：最后一段 = 完整答案
        # qwen3 等模型的 <think> 思考过程不属于答案，过滤掉
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.S).strip()
        return result or "助手未返回内容。"
    finally:
        try:
            chat.delete_sessions([session.id])
        except Exception:
            pass


# ---------- 直接检索（不依赖助手绑定，助手列表故障也能用） ----------

def _search_kb_sync(question: str, dataset_name: str, sid: str) -> str:
    """按名称定位知识库并直接检索原文片段。

    与 create_ask_delete（助手对话）完全解耦：只走 datasets 列举 + retrieval API，
    即使助手列表 403 / 助手绑定错库 / 助手不存在，也能拿到知识库原文。
    """
    # 1. 拉数据集列表（独立于助手，不调 _list_chats_http）
    try:
        all_ds = _list_datasets_http()
    except Exception as e:
        return (
            f"【知识库通道故障】无法列举数据集：{type(e).__name__}: {e}。"
            "请如实标注'知识库层通道故障、未采集'，禁止臆造该层内容。"
        )

    prefix = _prefix()
    session_ds_name = f"{prefix}{sid}" if sid else ""

    # 2. 选定目标库
    if not dataset_name:
        # 留空 = 全部内部库；排除会话级资料库（那是上一轮上传的临时资料）
        targets = [d for d in all_ds if not (d.get("name") or "").startswith(prefix)]
        scope = "全部内部库"
    elif dataset_name in ("会话资料库", session_ds_name):
        targets = [d for d in all_ds if d.get("name") == session_ds_name]
        scope = "会话资料库"
        if not targets:
            return "当前会话没有用户上传资料。"
    else:
        targets = [d for d in all_ds if dataset_name in (d.get("name") or "")]
        scope = dataset_name
        if not targets:
            names = "、".join(d.get("name", "") for d in all_ds) or "（无）"
            return (
                f"知识库「{dataset_name}」不存在。当前可用知识库：{names}。"
                "请从上述名称中选择后重试，禁止臆造内容。"
            )

    if not targets:
        return "没有可用的知识库数据集，请标注'知识库层未采集'。"

    # 3. 直连 retrieval API（_retrieval 内部已含 Redis 缓存 + rerank + 降级）
    try:
        chunks = _retrieval(question, [d["id"] for d in targets])
    except Exception as e:
        return f"【知识库检索故障】{type(e).__name__}: {e}。请标注'知识库层未采集'。"

    if not chunks:
        names = "、".join(d.get("name", "") for d in targets)
        return (
            f"在 {scope}（{names}）中未检索到与「{question}」相关的内容。"
            "请标注'知识库未命中'，禁止用常识补齐。"
        )

    # 4. 带来源 + 相似度返回，便于 agent 判断可信度并标注出处
    lines = [f"[直接检索：{scope} | 命中 {len(chunks)} 个片段]"]
    for i, c in enumerate(chunks, 1):
        score = c.get("similarity")
        score_str = f" 相似度{score:.2f}" if isinstance(score, (int, float)) else ""
        src = c.get("document", "") or "来源未知"
        lines.append(f"[片段{i} | 来源:{src}{score_str}] {c.get('content', '')}")
    return "\n".join(lines)


@tool
async def search_knowledge_base(question: str, dataset_name: str = "") -> str:
    """直接检索 RAGFlow 知识库原文片段（不经过助手，助手故障也能用）。

    这是内部知识库检索的主路径：按名称定位数据集后直连 retrieval API，
    返回带来源文档名与相似度的原文片段。即使 get_assistant_list 失败、
    助手绑定错库、或助手不存在，本工具仍然可用。

    :param question: 检索问题，尽量带具体关键词（品牌/型号/参数/价格带等）
    :param dataset_name: 知识库名称；留空=检索全部内部库；传"会话资料库"=检索本会话上传资料；
                         也可传具体名称（如 get_assistant_list 返回的关联知识库名）做包含匹配
    """
    monitor.report_tool(
        tool_name="知识库直接检索：search_knowledge_base",
        args={"question": question[:120], "dataset_name": dataset_name},
    )
    from core.context import get_session_id

    sid = get_session_id()
    try:
        return await asyncio.to_thread(_search_kb_sync, question, dataset_name, sid)
    except Exception as e:
        return f"知识库检索异常：{type(e).__name__}: {e}"
