"""节点：文档摘要 + 元数据（LLM 生成，随 MD 一起进 RAGFlow）。

目的（浏览器料层两个痛点）：
1. 全局性问题（"这份报告主要讲什么"）纯切片检索答不好 —— 摘要进库后可被整体检索；
2. 摘要作为元数据随文档入库，知识库助手回答时能引用。

设计取舍：
- 摘要**前置追加到 md 内容**并写回 md_path，这样 RAGFlow 切块时摘要天然成为第一块，
  检索"总结类"问题时能命中；不新增 RAGFlow 字段（SDK 元数据支持有限，写进正文最稳）。
- 摘要失败**不阻塞入库**：捕获所有异常，返回原 state，仅 warning。
"""
from pathlib import Path

from loguru import logger
from langchain_openai import ChatOpenAI

from core.config import cfg
from core.load_prompt import load_prompt
from import_pipeline.nodes.exceptions import FileProcessingError, ValidationError

SUMMARY_MARK = "<!-- doc-summary -->"


def node_document_summary(state: dict) -> dict:
    md_path = state.get("md_path", "")
    if not md_path:
        raise ValidationError("md 文件不存在", node_name="document_summary")
    path = Path(md_path)
    if not path.exists():
        raise FileProcessingError(f"md 路径无效：{md_path}", node_name="document_summary")

    content = path.read_text(encoding="utf-8")
    if SUMMARY_MARK in content:
        logger.info("[summary] 已存在摘要，跳过重复生成")
        return state

    # 只取前 N 字符做摘要，避免长文档撑爆上下文 / token 成本
    max_chars = int((cfg.get("import_pipeline") or {}).get("summary_max_chars", 12000))
    excerpt = content[:max_chars]

    try:
        prompt = load_prompt("document_summary", doc_name=path.stem, content=excerpt)
        llm = ChatOpenAI(
            api_key=cfg["llm"]["api_key"],
            base_url=cfg["llm"]["base_url"],
            model=cfg["llm"]["model"],
            temperature=0,
        )
        summary = (llm.invoke(prompt).content or "").strip()
    except Exception as e:
        logger.warning(f"[summary] 摘要生成失败，跳过（不影响入库）：{e}")
        return state

    if not summary:
        logger.warning("[summary] LLM 返回空摘要，跳过")
        return state

    block = f"{SUMMARY_MARK}\n\n## 文档摘要\n\n{summary}\n\n---\n\n"
    path.write_text(block + content, encoding="utf-8")
    state["summary"] = summary
    logger.info(f"[summary] 摘要已写入 {path.name}（{len(summary)} 字）")
    return state
