"""RAGFlow 知识库工具：内部助手（共享只读） + 用户上传库（会话级/账号级，计划书 3.3 混合方案）。"""
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
def get_assistant_list() -> str:
    """查询 RAGFlow 中可用的助手与知识库（含内部库与当前会话的用户上传资料库）。
    向某个助手提问前，必须先调用本工具获取助手名称。"""
    monitor.report_tool(tool_name="知识库助手列表：get_assistant_list", args={})
    try:
        cli = get_client()
        lines = []
        for chat in cli.list_chats():
            ds_names = []
            for ds in (chat.datasets or []):
                name = ds.get("name") if isinstance(ds, dict) else getattr(ds, "name", "")
                if name:
                    ds_names.append(name)
            lines.append(f"[内部库] 助手名称:{chat.name};功能介绍:{chat.description};关联知识库:{'、'.join(ds_names)}")
        # 当前会话的用户上传库
        from core.context import get_session_id

        sid = get_session_id()
        if sid:
            user_ds = [d for d in cli.list_datasets() if d.name == f"{_prefix()}{sid}"]
            if user_ds:
                lines.append(f"[会话资料库] 名称:{user_ds[0].name}（用户本会话上传的资料，可直接检索）")
        return "\n".join(lines) if lines else "没有任何可用助手"
    except Exception as e:
        return f"查询助手信息异常：{e}"


@tool
def create_ask_delete(chat_name: str, question: str) -> str:
    """向指定助手创建单次会话提问，问完即删。
    内部助手直接传 chat_name；若 chat_name 传"会话资料库"则对当前会话用户上传资料做深度检索。
    :param chat_name: get_assistant_list 返回的助手名称
    :param question: 提问内容
    """
    monitor.report_tool(tool_name="知识库提问：create_ask_delete", args={"chat_name": chat_name, "question": question[:120]})
    try:
        cli = get_client()
        # 会话资料库 → 直接走 retrieval API（无需临时 chat）
        from core.context import get_session_id

        sid = get_session_id()
        if sid and (chat_name == "会话资料库" or chat_name == f"{_prefix()}{sid}"):
            ds = cli.list_datasets(name=f"{_prefix()}{sid}")
            if not ds:
                return "当前会话没有用户上传资料。"
            chunks = _retrieval(question, [ds[0].id])
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

        chats = cli.list_chats(name=chat_name)
        if not chats:
            return f"助手 {chat_name} 不存在，请先调用 get_assistant_list 确认名称。"
        session = chats[0].create_session(name="temp_session_ask")
        try:
            result = ""
            for piece in session.ask(question=question, stream=True):
                content = piece.content if hasattr(piece, "content") else str(piece)
                if content:
                    result += content
            return result or "助手未返回内容。"
        finally:
            # 用完即删，保持助手会话列表干净
            try:
                chats[0].delete_sessions([session.id])
            except Exception:
                pass
    except Exception as e:
        return f"提问异常：{e}"
