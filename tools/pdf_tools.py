"""MD → PDF 转换（Windows 下优先 Word COM 引擎；失败时按约定降级返回 md 路径，禁止静默失败）。"""
from langchain_core.tools import tool

from api.monitor import monitor
from core.context import get_session_dir


@tool
def convert_md_to_pdf(filename: str) -> str:
    """将当前会话工作目录中的 Markdown 报告转换为 PDF。
    仅在用户明确要求导出 PDF 时调用。
    :param filename: 已存在的 md 文件名（相对会话目录）
    :return: 成功返回 PDF 路径；失败返回错误说明与 md 路径（降级）
    """
    monitor.report_tool(tool_name="PDF导出：convert_md_to_pdf", args={"filename": filename})
    from pathlib import Path

    session_dir = get_session_dir()
    if not session_dir:
        return "错误：当前没有会话工作目录。"
    md_path = Path(session_dir) / Path(filename).name
    if not md_path.exists():
        return f"错误：找不到文件 {filename}，请先用 generate_markdown 生成报告。"

    pdf_path = md_path.with_suffix(".pdf")
    try:
        from utils.word_converter import convert_md_to_pdf_via_word

        result = convert_md_to_pdf_via_word(md_path, pdf_path)
        if pdf_path.exists():
            return f"PDF 已生成：{pdf_path.name}"
        return f"PDF 转换失败：{result}。降级：请使用 Markdown 报告文件 {md_path.name}。"
    except Exception as e:
        return f"PDF 转换异常：{e}。降级：请使用 Markdown 报告文件 {md_path.name}。"
