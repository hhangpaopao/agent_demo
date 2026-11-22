"""deep_pro API 服务（WebSocket × ContextVar 直连版，沿用深度项目模式）。

连接协议（/ws/{session_id}）：
- 客户端 → 服务端 JSON 消息：
    {"type": "chat", "query": "..."}       发起研究任务
    {"type": "interrupt"}                  中断当前任务（状态保留）
    {"type": "resume"}                     从断点继续执行（决策7，需 PostgreSQL checkpointer）
- 服务端 → 客户端 JSON 事件（Monitor 经 ContextVar 匹配连接直推）：
    {"event": "session_dir|assistant|tool|result|pipeline|error|done", "data": {...}, "ts": ...}

会话持续性：断线重连同 session_id → checkpointer(thread_id) 恢复历史，
工作目录与会话资料库保持有效。
其余 REST：上传资料 / 入库状态 / 按需导出 PDF。
"""
import asyncio
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect

from agent.main_agent import run_deep_agent, init_agent, close_agent
from api.monitor import monitor, manager, build_event
from core.config import PROJECT_ROOT, cfg
from core.context import reset_session_context, set_session_context, set_thread_context
from tools.tavily_tool import reset_search_budget
from tools.db_fallback import reset_db_budget

app = FastAPI(title="deep_pro", version="0.1.0")

# 入库任务状态表（演示用内存表；生产建议落 Redis/DB）
_upload_tasks: dict[str, dict] = {}


@app.on_event("startup")
async def startup():
    await init_agent()  # 决策7：装配持久化 checkpointer（失败自动降级）
    asyncio.create_task(_ttl_cleanup_loop())


@app.on_event("shutdown")
async def shutdown():
    await close_agent()
    from tools.wenshu_runner import shutdown_wenshu

    await asyncio.to_thread(shutdown_wenshu)  # 关闭问数图的异步连接池 + 后台 loop


async def _ttl_cleanup_loop():
    """TTL 惰性清理：每小时清理过期会话 dataset（计划书 3.3）。"""
    from tools.ragflow_tools import cleanup_expired_datasets

    while True:
        try:
            removed = cleanup_expired_datasets()
            if removed:
                print(f"[ttl_cleanup] 已清理过期 dataset：{removed}")
        except Exception as e:
            print(f"[ttl_cleanup] 异常（不影响主流程）：{e}")
        await asyncio.sleep(3600)


@app.websocket("/ws/{session_id}")
async def ws_endpoint(ws: WebSocket, session_id: str):
    """会话主通道：reader 接收指令，Monitor 通过 ContextVar 直推事件。"""
    loop = asyncio.get_running_loop()
    manager.set_loop(loop)          # 绑定本次连接所在的事件循环
    await manager.connect(ws, session_id)   # accept + 登记 session_id → WS
    agent_task: asyncio.Task | None = None
    ctx_tokens = None

    async def _run_agent(query: str, resume: bool = False):
        nonlocal ctx_tokens
        reset_search_budget()
        reset_db_budget()  # 决策1：每轮任务重置 nl2sql 尝试预算与候选表缓存
        try:
            ctx_tokens = (
                set_session_context(str(PROJECT_ROOT / cfg["paths"]["output_dir"] / f"session_{session_id}")),
                set_thread_context(session_id),
            )
            await run_deep_agent(query, session_id, resume=resume)
            monitor.report_done({"session_id": session_id})
        except asyncio.CancelledError:
            # 决策7：中断不销毁状态，用户可用 {"type":"resume"} 从断点续跑
            monitor.report_interrupted()
            raise
        except Exception as e:
            monitor.report_error(f"执行主智能体发生异常：{e}")
        finally:
            if ctx_tokens:
                reset_session_context(*ctx_tokens)
                ctx_tokens = None

    try:
        while True:
            msg = await ws.receive_json()
            mtype = msg.get("type")
            if mtype == "chat":
                if agent_task and not agent_task.done():
                    await ws.send_json(build_event("error", "已有任务在执行，请先等待完成或发送 interrupt 中断", session_id=session_id))
                    continue
                query = msg.get("query", "").strip()
                if not query:
                    await ws.send_json(build_event("error", "query 不能为空", session_id=session_id))
                    continue
                agent_task = asyncio.create_task(_run_agent(query))
            elif mtype == "resume":
                if agent_task and not agent_task.done():
                    await ws.send_json(build_event("error", "任务仍在执行，无需 resume", session_id=session_id))
                    continue
                agent_task = asyncio.create_task(_run_agent("", resume=True))
            elif mtype == "interrupt":
                if agent_task and not agent_task.done():
                    agent_task.cancel()
                else:
                    await ws.send_json(build_event("error", "当前没有正在执行的任务", session_id=session_id))
    except WebSocketDisconnect:
        pass
    finally:
        if agent_task and not agent_task.done():
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):
                pass
        if ctx_tokens:
            reset_session_context(*ctx_tokens)
        manager.disconnect(session_id)


@app.post("/api/upload_material")
async def upload_material(session_id: str, file: UploadFile = File(...)):
    """上传用户资料：pdf/md。后台执行裁剪版入库管线，进度经 WS pipeline 事件推送。"""
    max_mb = int(cfg["upload"]["max_mb"])
    if file.size and file.size > max_mb * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {max_mb}MB 限制")

    upload_dir = PROJECT_ROOT / cfg["paths"]["upload_dir"] / f"session_{session_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = upload_dir / Path(file.filename).name
    with open(save_path, "wb") as f:
        f.write(await file.read())

    task_id = uuid.uuid4().hex
    file_name = Path(file.filename).name
    _upload_tasks[task_id] = {"status": "running", "session_id": session_id, "file": file_name}

    async def _run():
        from import_pipeline.pipeline import run_import_pipeline

        tokens = (set_session_context(str(PROJECT_ROOT / cfg["paths"]["output_dir"] / f"session_{session_id}")),
                  set_thread_context(session_id))
        try:
            # 推送"上传ing"：前端显示常驻指示，提示用户资料尚未可检索
            monitor.report_upload("uploading", task_id, file_name, "资料上传中，入库完成后方可检索")
            result = await run_import_pipeline(session_id, save_path)
            _upload_tasks[task_id].update(status="done", md_path=result["md_path"])
            monitor.report_upload("done", task_id, file_name, "资料入库完成，现在可以检索")
        except Exception as e:
            _upload_tasks[task_id].update(status="failed", error=str(e))
            monitor.report_pipeline("failed", f"入库失败：{e}")
            monitor.report_upload("failed", task_id, file_name, f"入库失败：{e}")
        finally:
            reset_session_context(*tokens)

    asyncio.create_task(_run())
    return {"task_id": task_id, "status": "running", "message": "资料上传中，入库完成后可在知识库中检索"}


@app.get("/api/upload_status/{task_id}")
async def upload_status(task_id: str):
    if task_id not in _upload_tasks:
        raise HTTPException(404, "任务不存在")
    return _upload_tasks[task_id]


@app.post("/api/report/pdf")
async def report_pdf(session_id: str, filename: str):
    """导出报告 PDF：按需触发（计划书 3.4），复用 Word COM 转换器。"""
    session_dir = PROJECT_ROOT / cfg["paths"]["output_dir"] / f"session_{session_id}"
    md_path = session_dir / Path(filename).name
    if not md_path.exists():
        raise HTTPException(404, f"找不到报告 {filename}")
    pdf_path = md_path.with_suffix(".pdf")
    try:
        from utils.word_converter import convert_md_to_pdf_via_word

        convert_md_to_pdf_via_word(md_path, pdf_path)
    except Exception as e:
        raise HTTPException(500, f"PDF 转换失败，请使用 Markdown 报告：{e}")
    if not pdf_path.exists():
        raise HTTPException(500, "PDF 转换未产出文件，请使用 Markdown 报告")
    return {"pdf": str(pdf_path.name)}
