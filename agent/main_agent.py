"""主智能体：初始化（持久化 checkpointer）+ 流式执行 + 中断恢复。

决策7：对话状态落 PostgreSQL（AsyncPostgresSaver），因此
- 服务重启后会话历史仍在；
- 用户中断任务后，状态保留在最后一次 checkpoint，可用 {"type":"resume"} 续跑。
PostgreSQL 不可用时自动降级 InMemorySaver（仅进程内），并在日志中告警。
"""
from loguru import logger

from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver

from agent.llm import model
from agent.prompts import main_agent_content
from agent.subagents.analysis_agent import analysis_agent
from agent.subagents.database_query_agent import database_query_agent
from agent.subagents.knowledge_base_agent import knowledge_base_agent
from agent.subagents.network_search_agent import network_search_agent
from agent.subagents.verification_agent import verification_agent
from core.config import PROJECT_ROOT, cfg
from core.context import set_session_context, set_thread_context, reset_session_context
from api.monitor import monitor
from tools.markdown_tools import generate_markdown
from tools.pdf_tools import convert_md_to_pdf
from tools.upload_file_read_tool import read_file_content

_agent = None
_saver = None
_pool = None


def _build_agent(checkpointer):
    return create_deep_agent(
        model=model,
        system_prompt=main_agent_content["system_prompt"],
        tools=[generate_markdown, convert_md_to_pdf, read_file_content],
        checkpointer=checkpointer,
        subagents=[
            database_query_agent,
            network_search_agent,
            knowledge_base_agent,
            analysis_agent,
            verification_agent,
        ],
    )


async def init_agent():
    """服务启动时调用一次：装配 checkpointer 与主智能体。"""
    global _agent, _saver, _pool
    if _agent is not None:
        return _agent

    backend = str((cfg.get("checkpoint") or {}).get("backend", "memory")).lower()
    if backend == "postgres":
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg_pool import AsyncConnectionPool

            dsn = cfg["checkpoint"]["postgres_dsn"]
            _pool = AsyncConnectionPool(conninfo=dsn, max_size=5, open=False)
            await _pool.open()
            _saver = AsyncPostgresSaver(_pool)
            await _saver.setup()
            logger.info("[checkpoint] 已启用 PostgreSQL 持久化（支持重启恢复 / 中断续跑）")
        except Exception as e:
            logger.warning(f"[checkpoint] PostgreSQL 不可用，降级 InMemorySaver（重启会丢历史）：{e}")
            _pool = None
            _saver = InMemorySaver()
    else:
        logger.info("[checkpoint] 使用 InMemorySaver（进程内）")
        _saver = InMemorySaver()

    _agent = _build_agent(_saver)
    return _agent


def get_agent():
    if _agent is None:
        raise RuntimeError("主智能体未初始化：请在应用启动时 await init_agent()")
    return _agent


async def close_agent():
    """服务关闭时调用：释放连接池。"""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as e:
            logger.warning(f"[checkpoint] 关闭连接池异常：{e}")
        _pool = None


async def run_deep_agent(task_query: str, session_id: str, resume: bool = False):
    """流式+异步执行主智能体。

    :param resume: True 时不传新输入，从最后一次 checkpoint 继续（中断恢复）
    """
    agent = get_agent()

    session_dir = PROJECT_ROOT / cfg["paths"]["output_dir"] / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_dir_str = str(session_dir).replace("\\", "/")
    relative_dir = str(session_dir.relative_to(PROJECT_ROOT)).replace("\\", "/")

    uploaded_prompt = ""
    upload_dir = PROJECT_ROOT / cfg["paths"]["upload_dir"] / f"session_{session_id}"
    if upload_dir.exists():
        import shutil

        files = [f.name for f in upload_dir.iterdir() if f.is_file()]
        if files:
            for name in files:
                shutil.copy2(upload_dir / name, session_dir / name)
            uploaded_prompt = (
                "\n[已上传文件] 已加载到工作目录:\n"
                + "\n".join(f"    - {f}" for f in files)
                + "\n    请优先使用工具（read_file_content）读取并参考这些文件。"
            )

    dir_token = set_session_context(session_dir_str)
    id_token = set_thread_context(session_id)
    monitor.report_session_dir(session_dir_str)

    path_instruction = f"""
    【工作环境指令】
    工作目录: {relative_dir}
    {uploaded_prompt}
    规则：
    1. 新生成文件必须保存到工作目录：'{relative_dir}/filename'
    2. 读取已上传文件时直接传文件名给 read_file_content，不要带目录前缀
    3. 使用相对路径，禁止使用绝对路径
    4. 若存在上传文件，请先分析内容
    """
    config = {"configurable": {"thread_id": session_id}}

    if resume:
        stream_input = None  # 从 checkpoint 续跑
        monitor.report_tool(tool_name="会话续跑：从断点恢复执行", args={"session_id": session_id})
    else:
        stream_input = {"messages": [{"role": "user", "content": task_query + path_instruction}]}

    try:
        async for chunk in agent.astream(stream_input, config=config):
            for node_name, state in chunk.items():
                if not state or "messages" not in state:
                    continue
                messages = state["messages"]
                if not (messages and isinstance(messages, list)):
                    continue
                last_msg = messages[-1]
                if node_name == "model":
                    if getattr(last_msg, "tool_calls", None):
                        for tc in last_msg.tool_calls:
                            if tc["name"] == "task":
                                monitor.report_assistant(
                                    tc["args"]["subagent_type"],
                                    {"description": tc["args"]["description"]},
                                )
                    elif last_msg.content:
                        monitor.report_task_result(last_msg.content)
                elif node_name == "tools":
                    # 工具返回（ToolMessage）：补 tool_end，避免前端进度条只有开始没有结束
                    tool_name = getattr(last_msg, "name", None) or getattr(last_msg, "tool_call_id", "")
                    if tool_name:
                        content = last_msg.content if isinstance(last_msg.content, str) else str(last_msg.content)
                        monitor.report_tool_end(tool_name, content[:120])
    except Exception as e:
        monitor.report_error(f"执行主智能体发生异常：{e}")
    finally:
        reset_session_context(dir_token, id_token)
