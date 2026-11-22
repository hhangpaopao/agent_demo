"""节点：Markdown 图片处理（VLM 转写标题 + MinIO 存图 + 回填 md）。

从 shopkeeper_brain/knowledge/processor/import_process/nodes/md_img_node.py 移植，
按本项目约定改造：
- 类方法 process(state) → 函数 node_md_img(state)，与 pipeline.py 调用方式一致
- BaseNode/self.logger → loguru；去掉 knowledge.* 包依赖
- config → 项目统一 cfg；minio_util → 本目录 minio_util.py
- **修复原实现 bug**：shopkeeper 把转写结果写到 {stem}_new.md 备份、只放进 state['md_content']，
  但上传 RAGFlow 用的是原 md_path —— 图片转写结果根本没进库。
  本实现把转写后的内容**写回原 md_path**，保证入库内容 = 用户看到的内容。

输入 state: {"md_path": str}
输出 state: 原样 + {"md_content": str}
"""
import base64
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Deque, List, Tuple

from loguru import logger
from openai import OpenAI

from core.config import cfg
from import_pipeline.nodes.exceptions import (
    FileProcessingError,
    ImageProcessingError,
    ValidationError,
)
from import_pipeline.nodes.minio_util import get_external_base_url, get_minio_client

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


def node_md_img(state: dict) -> dict:
    md_path = state.get("md_path", "")
    if not md_path:
        raise ValidationError("md 文件不存在", node_name="md_img")
    path = Path(md_path)
    if not path.exists():
        raise FileProcessingError(f"md 路径无效：{md_path}", node_name="md_img")

    md_content = path.read_text(encoding="utf-8")
    image_dir = path.parent / "images"

    if not image_dir.exists():
        logger.warning(f"[md_img] {path.name} 无 images 目录，跳过图片处理")
        state["md_content"] = md_content
        return state

    vlm = _vlm_config()
    targets = _scan_images_and_context(image_dir, md_content, vlm["img_context_length"])
    if not targets:
        logger.info("[md_img] 未找到可处理的图片引用")
        state["md_content"] = md_content
        return state

    summaries = _extract_img_summary(path.stem, targets, vlm)
    new_md = _upload_img_and_update_md(path.stem, md_content, summaries, targets)

    # 关键修复：写回原 md_path，让后续上传 RAGFlow 的正是转写后的内容
    try:
        path.write_text(new_md, encoding="utf-8")
    except IOError as e:
        raise ImageProcessingError(f"写回 md 失败：{e}", node_name="md_img") from e

    state["md_content"] = new_md
    logger.info(f"[md_img] 处理完成：{len(targets)} 张图片，已写回 {path.name}")
    return state


def _vlm_config() -> dict:
    """VLM 配置：优先 vlm 段；未配置则复用 llm（开箱即用）。"""
    llm = cfg["llm"]
    vlm = cfg.get("vlm") or {}
    return {
        "api_key": vlm.get("api_key") or llm["api_key"],
        "base_url": vlm.get("base_url") or llm["base_url"],
        "model": vlm.get("model") or llm["model"],
        "img_context_length": int((cfg.get("import_pipeline") or {}).get("img_context_length", 200)),
        "requests_per_minute": int((cfg.get("import_pipeline") or {}).get("vlm_rpm", 15)),
    }


def _scan_images_and_context(
    image_dir: Path, md_content: str, max_chars: int
) -> List[Tuple[str, str, Tuple[str, str, str]]]:
    """扫描图片目录，为每张图片提取 md 中的上下文（最近标题 / 上文 / 下文）。"""
    targets = []
    for img_name in os.listdir(image_dir):
        if os.path.splitext(img_name)[1].lower() not in IMAGE_EXTENSIONS:
            continue
        contexts = _find_img_context(md_content, img_name, max_chars)
        if not contexts:
            logger.warning(f"[md_img] {img_name} 在 md 中无引用或无法提取上下文，跳过")
            continue
        targets.append((img_name, str(image_dir / img_name), contexts[0]))
    logger.info(f"[md_img] 找到 {len(targets)} 张可处理图片")
    return targets


def _find_img_context(md_content: str, img_name: str, max_chars: int) -> List[Tuple[str, str, str]]:
    pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(img_name) + r".*?\)")
    lines = md_content.split("\n")
    found = []
    for idx, line in enumerate(lines):
        if not pattern.search(line):
            continue
        # 最近标题
        head_title, head_index = "", -1
        for i in range(idx - 1, -1, -1):
            if re.match(r"^#{1,6}\s+", lines[i]):
                head_title, head_index = lines[i], i
                break
        pre = _extract_paragraphs(lines[head_index + 1: idx], max_chars, direction="front")
        # 下文到下一个标题为止
        end = len(lines)
        for i in range(idx + 1, len(lines)):
            if re.match(r"^#{1,6}\s+", lines[i]):
                end = i
                break
        post = _extract_paragraphs(lines[idx + 1: end], max_chars, direction="end")
        found.append((head_title, pre, post))
    return found


def _extract_paragraphs(content: list, max_chars: int, direction: str) -> str:
    current, paragraphs = [], []
    for line in content:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                current = []
        elif re.match(r"^!\[.*?\]\(.*?\)$", stripped):
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        paragraphs.append("\n".join(current))
    if direction == "front":
        paragraphs.reverse()
    total, selected = 0, []
    for para in paragraphs:
        if total + len(para) > max_chars and selected:
            break
        selected.append(para)
        total += len(para)
    if direction == "front":
        selected.reverse()
    return "\n\n".join(selected)


def _extract_img_summary(document_title: str, targets: list, vlm: dict) -> dict:
    """VLM 生成中文标题；带速率限制；失败返回兜底文案，不中断主流程。"""
    summaries = {}
    try:
        client = OpenAI(api_key=vlm["api_key"], base_url=vlm["base_url"])
    except Exception as e:
        logger.warning(f"[md_img] VLM 客户端创建失败：{e}，全部使用兜底标题")
        return {name: "图片描述" for name, _, _ in targets}

    timestamps: Deque[float] = deque()
    rpm = vlm["requests_per_minute"]
    for name, img_path, context in targets:
        _enforce_rate_limit(timestamps, rpm)
        summaries[name] = _get_one_summary(client, vlm["model"], document_title, img_path, context)
    return summaries


def _enforce_rate_limit(timestamps: Deque[float], max_requests: int, window: int = 60):
    now = time.time()
    while timestamps and now - timestamps[0] >= window:
        timestamps.popleft()
    if len(timestamps) >= max_requests:
        sleep_for = window - (now - timestamps[0])
        if sleep_for > 0:
            logger.info(f"[md_img] 触发 VLM 限流，等待 {sleep_for:.1f}s")
            time.sleep(sleep_for)
        now = time.time()
        while timestamps and now - timestamps[0] >= window:
            timestamps.popleft()
    timestamps.append(now)


def _get_one_summary(client, model: str, doc_title: str, img_path: str, context: Tuple[str, str, str]) -> str:
    section_title, pre, post = context
    parts = [p for p in (section_title, pre, post) if p]
    final_context = "\n".join(parts) or "暂无可用上下文"
    try:
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return "暂无图片"

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "任务：为 Markdown 文档中的图片生成一个简短中文标题。\n"
                        f"文档标题：{doc_title}\n图片上下文：{final_context}\n"
                        "请结合图片视觉内容与上下文，用中文总结并生成精准标题（不要包含“图片”二字）。"
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
        )
        return (resp.choices[0].message.content or "").strip() or "图片描述"
    except Exception as e:
        logger.warning(f"[md_img] VLM 摘要失败 {Path(img_path).name}：{e}")
        return "图片描述"


def _upload_img_and_update_md(
    document_name: str, md_content: str, summaries: dict, targets: list
) -> str:
    """上传图片到 MinIO，替换 md 中的本地路径与 alt 文本。"""
    client = get_minio_client()
    if client is None:
        logger.warning("[md_img] MinIO 不可用，跳过图片上传（md 保留原本地路径）")
        return md_content

    base_url = get_external_base_url()
    remote_urls = {}
    for img_name, img_path, _ in targets:
        object_name = f"{document_name}/{img_name}"
        try:
            client.fput_object(cfg["minio"]["bucket"], object_name, img_path)
            remote_urls[img_name] = f"{base_url}/{object_name}"
        except Exception as e:
            logger.warning(f"[md_img] 上传图片失败 {img_name}：{e}，使用 mock 链接")
            remote_urls[img_name] = f"http://minio-mock/{document_name}/{img_name}"

    new_md = md_content
    for img_name, summary in summaries.items():
        url = remote_urls.get(img_name)
        if not url:
            continue
        pattern = re.compile(r"!\[(.*?)\]\((.*?" + re.escape(img_name) + r".*?)\)", re.IGNORECASE)
        new_md = pattern.sub(f"![{summary}]({url})", new_md)
    return new_md
