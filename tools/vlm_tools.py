"""VLM 主图解析工具：读竞品商品主图上的文案与元素布局。

为什么需要它：网络层能拿回图片直链，但模型看不见图 —— 而竞品主图的价值恰恰在
「文案怎么写、卖点怎么陈述、元素怎么摆」（见竞品主图分析方法论）。所以用 VLM
（qwen-vl-ocr）把图上的文字连同坐标读出来，再按坐标反推布局分区，
让研判层能真正对比主图，而不是只看图好不好看。

成本闸门：会话级硬计数（默认 10 次 = 5 轮检索 × 每轮 2 张），超限直接拦截。
RPM 限速靠 provider 侧 VLM_RPM，本工具串行调用不额外并发。
"""
import asyncio
import os
from contextvars import ContextVar

from langchain_core.tools import tool
from loguru import logger

from api.monitor import monitor

_vlm_state: ContextVar[dict | None] = ContextVar("vlm_state", default=None)

_MAX_READS_PER_SESSION = 10  # 5 轮检索 × 每轮最多 2 张

_PROMPT = (
    "请提取这张商品主图上的全部文字（标题、卖点、参数、促销语、品牌名）。"
    "严格按格式逐行输出：x,y,w,h,文字 —— 坐标为文字框左上角 x/y 与宽高 w/h，"
    "全部归一化到 0-1000 的相对坐标。不要解释、不要总结、不要补全图上没有的字。"
)


def reset_vlm_budget():
    """每轮任务开始时重置图片解析计数。"""
    _vlm_state.set({"calls": 0})


def _zone(x: float, y: float, span: float = 1000.0) -> str:
    """把坐标映射成九宫格分区（上/中/下 × 左/中/右），用于反推主图布局。"""
    x, y = x / span * 1000, y / span * 1000
    v = "上" if y < 333 else ("中" if y < 667 else "下")
    h = "左" if x < 333 else ("中" if x < 667 else "右")
    return f"{v}{h}"


def _parse_ocr(raw: str) -> list[str]:
    """把 OCR 输出转成「文本（分区）」列表。

    兼容两种形态：`x,y,w,h,text`（新 prompt）与 `x,y,w,h,conf,text`（模型自带格式）；
    若返回纯文本（无坐标），按行原样保留，不臆造位置。
    若坐标明显是像素值（>1000），按本批最大值做相对归一化，保证分区仍反映相对位置。
    """
    rows: list[tuple[float, float, str]] = []
    plain: list[str] = []

    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",", 5)]
        nums = [p for p in parts if p.replace(".", "", 1).isdigit()]
        if len(parts) >= 5 and len(nums) >= 4:
            try:
                x, y = float(parts[0]), float(parts[1])
            except ValueError:
                plain.append(line)
                continue
            text = ",".join(parts[4:]).strip()
            if text.split(",")[0].strip().replace(".", "", 1).isdigit():
                text = ",".join(text.split(",")[1:]).strip()
            if text:
                rows.append((x, y, text))
                continue
        plain.append(line)

    if not rows:
        return plain

    span = max(max(x for x, _, _ in rows), max(y for _, y, _ in rows))
    span = 1000.0 if span <= 1000 else span  # 像素坐标 → 按本批极值相对归一化
    texts = [f"{t}（{_zone(x, y, span)}）" for x, y, t in rows]
    return texts + plain


def _analyze_sync(image_url: str) -> str:
    """阻塞调用 VLM 读图（由 async 工具丢进线程池执行）。"""
    from openai import OpenAI

    key = os.getenv("VLM_API_KEY")
    base = os.getenv("VLM_BASE_URL")
    model = os.getenv("VLM_MODEL")
    if not (key and base and model):
        raise RuntimeError("VLM 未配置（VLM_API_KEY / VLM_BASE_URL / VLM_MODEL）")

    client = OpenAI(api_key=key, base_url=base, timeout=60)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_url}},
                {"type": "text", "text": _PROMPT},
            ],
        }],
        temperature=0,
    )
    return (resp.choices[0].message.content or "").strip()


@tool
async def analyze_product_image(image_url: str, brand: str = "") -> str:
    """解析竞品商品主图：读出图上的卖点文案与各元素所在分区，用于主图对比分析。

    :param image_url: 图片直链（须来自 internet_search 的 images 字段，http/https）
    :param brand: 该图对应的竞品品牌名（用于结果标注，可留空）
    :return: 主图文案清单（含九宫格分区），或明确的失败说明
    """
    if not isinstance(image_url, str) or not image_url.startswith("http"):
        return "解析失败：image_url 必须是 http/https 图片直链。"

    state = _vlm_state.get()
    if state is None:
        state = {"calls": 0}
        _vlm_state.set(state)
    if state["calls"] >= _MAX_READS_PER_SESSION:
        return (
            f"已达本会话主图解析上限（{_MAX_READS_PER_SESSION} 次），"
            "请基于已解析的主图结论分析，不要再读新图。"
        )
    state["calls"] += 1

    monitor.report_tool(
        tool_name="主图解析：analyze_product_image",
        args={"brand": brand or "未知", "url": image_url[:90], "call": f"{state['calls']}/{_MAX_READS_PER_SESSION}"},
    )

    try:
        raw = await asyncio.to_thread(_analyze_sync, image_url)
    except Exception as e:
        logger.warning(f"主图解析失败 {image_url[:80]}: {e}")
        return f"主图解析失败（{type(e).__name__}）：{str(e)[:120]}。可跳过该图，不要臆造图上内容。"

    items = _parse_ocr(raw)
    if not items:
        return "主图解析结果为空（图可能无文字或加载失败），请如实标注，不要臆造文案。"

    head = f"[主图解析 | 品牌：{brand or '未标注'} | 图：{image_url}]"
    return head + "\n" + "\n".join(f"- {it}" for it in items)
