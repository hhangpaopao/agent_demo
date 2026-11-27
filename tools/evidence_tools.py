"""研判证据中间文件：采集层落盘 → 研判/核验层读取（借鉴 deep-research-agent 的 state["files"]）。

设计要点：
- 每个采集层把结果写到会话工作目录的 evidence/{layer}/ 下：
    findings.md（浓缩结论，研判 agent 读）、raw.md（原始证据，核验 agent 读）、
    sources.json（来源清单，供报告溯源）；
- index.md 记录三层落盘状态，研判 agent 据此判断是否"某层缺失"；
- 读写都限制在会话目录内，路径用 Path.name 防越权（与 read_file_content 一致的安全口径）。

注意：本项目的 read_file_content 只取 Path(filename).name，无法读子目录文件，
所以证据读取单独提供 read_evidence(layer, kind) —— 参数化而非传路径，更安全。

跨线程修复：工具改为 async，在事件循环内读取会话 ContextVar（同步工具会被
LangGraph 丢进线程池，Python 3.12 以下 ContextVar 不传播 → 拿到空目录）。
"""
import asyncio
import json
from pathlib import Path

from langchain_core.tools import tool

from api.monitor import monitor
from core.context import get_session_dir

_LAYERS = ("data", "network", "document")
_KINDS = ("findings", "raw", "sources")


def _evidence_root() -> Path:
    session_dir = get_session_dir()
    if not session_dir:
        raise RuntimeError("当前没有会话工作目录，无法读写证据文件")
    return Path(session_dir) / "evidence"


def _safe_layer(layer: str) -> str:
    value = (layer or "").strip().lower()
    if value not in _LAYERS:
        raise ValueError(f"layer 必须是 {_LAYERS} 之一，收到：{layer}")
    return value


@tool
async def save_evidence(layer: str, findings: str, raw: str, sources: str = "[]") -> str:
    """采集完成后落盘本层证据（供后续研判与核验读取）。

    :param layer: 采集层，只能是 data（数据库）/ network（网络）/ document（文档知识库）之一
    :param findings: 浓缩结论——本层的关键发现与数字，研判 agent 读它
    :param raw: 原始证据——SQL 全文+结果行 / 网页标题+URL+片段 / 检索片段+来源，核验 agent 读它
    :param sources: 来源清单 JSON 字符串，如 [{"id":"s1","desc":"订单表 orders"}]，可留空
    :return: 落盘结果说明
    """
    layer = _safe_layer(layer)
    monitor.report_tool(tool_name=f"证据落盘：save_evidence({layer})", args={"layer": layer})
    # root 必须在事件循环内取（读会话 ContextVar），之后的纯文件 IO 丢线程池
    root = _evidence_root()
    await asyncio.to_thread(_write_evidence, root, layer, findings, raw, sources)
    return f"证据已落盘：evidence/{layer}/（findings.md / raw.md / sources.json），index.md 已更新"


def _write_evidence(root: Path, layer: str, findings: str, raw: str, sources: str) -> None:
    """同步写盘：由 save_evidence 经 to_thread 调用，不读任何 ContextVar。"""
    layer_dir = root / layer
    layer_dir.mkdir(parents=True, exist_ok=True)
    (layer_dir / "findings.md").write_text(findings.strip() + "\n", encoding="utf-8")
    (layer_dir / "raw.md").write_text(raw.strip() + "\n", encoding="utf-8")
    try:
        parsed = json.loads(sources) if sources.strip() else []
        if not isinstance(parsed, list):
            parsed = []
    except (ValueError, TypeError):
        parsed = []
    (layer_dir / "sources.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# 证据索引", ""]
    for name in _LAYERS:
        d = root / name
        ready = (d / "findings.md").exists() and (d / "raw.md").exists()
        lines.append(f"- {name}: {'已采集' if ready else '缺失'}")
    lines.append("")
    (root / "index.md").write_text("\n".join(lines), encoding="utf-8")


@tool
async def read_evidence(layer: str, kind: str) -> str:
    """读取某层的证据文件，供研判/核验使用。

    :param layer: data / network / document 之一
    :param kind: findings（浓缩结论）/ raw（原始证据）/ sources（来源清单）/ index（全局索引）
    :return: 文件内容；不存在时返回明确提示（调用方据此标注"该层缺失"）
    """
    kind = (kind or "").strip().lower()
    if kind == "index":
        monitor.report_tool(tool_name="证据读取：read_evidence(index)", args={"kind": kind})
        path = _evidence_root() / "index.md"
    else:
        layer = _safe_layer(layer)
        if kind not in _KINDS:
            return f"错误：kind 必须是 {_KINDS} 或 index，收到：{kind}"
        monitor.report_tool(tool_name=f"证据读取：read_evidence({layer}/{kind})", args={"layer": layer, "kind": kind})
        suffix = ".json" if kind == "sources" else ".md"
        path = _evidence_root() / layer / f"{kind}{suffix}"
    if not path.exists():
        return f"证据缺失：{path.name} 未落盘。若为某层不存在，请在报告中标注'该层未采集'，不要臆造。"
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return text[:50000]
    except UnicodeDecodeError:
        return f"证据文件 {path.name} 非文本格式，无法读取。"
