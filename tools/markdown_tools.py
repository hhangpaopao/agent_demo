"""报告生成：Markdown 落盘到会话工作目录（计划书 3.4 输出物约定）。"""
from langchain_core.tools import tool

from api.monitor import monitor
from core.context import get_session_dir


@tool
def generate_markdown(filename: str, content: str) -> str:
    """生成 Markdown 报告文件并保存到当前会话工作目录。
    :param filename: 文件名（如 销售分析报告.md），使用相对路径，禁止绝对路径
    :param content: 完整的 Markdown 内容（数据用表格，图片用提供的 MinIO 链接）
    :return: 保存结果说明
    """
    monitor.report_tool(tool_name="报告生成：generate_markdown", args={"filename": filename})
    from pathlib import Path

    session_dir = get_session_dir()
    if not session_dir:
        return "错误：当前没有会话工作目录，无法保存文件。"
    path = Path(session_dir) / Path(filename).name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"报告已保存：{filename}（会话目录内）。如需 PDF 请用户明确要求后再调用 convert_md_to_pdf。"
