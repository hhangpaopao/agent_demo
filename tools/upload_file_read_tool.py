"""读取用户上传的文件内容（从会话工作目录）。

跨线程修复：工具改为 async，在事件循环内读取会话目录 ContextVar。
"""
import asyncio
from langchain_core.tools import tool

from api.monitor import monitor
from core.context import get_session_dir


@tool
async def read_file_content(filename: str) -> str:
    """读取当前会话中已上传/已生成文件的内容。
    :param filename: 文件名（相对会话目录，不要带目录前缀）
    :return: 文件文本内容（二进制类文件返回提示）
    """
    monitor.report_tool(tool_name="文件读取：read_file_content", args={"filename": filename})
    from pathlib import Path

    session_dir = get_session_dir()
    if not session_dir:
        return "错误：当前没有会话工作目录。"
    path = (Path(session_dir) / Path(filename).name).resolve()
    if not path.exists():
        return f"错误：文件 {filename} 不存在。"
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return text[:50000]
    except UnicodeDecodeError:
        return f"文件 {filename} 为二进制格式，无法直接读取文本。"
