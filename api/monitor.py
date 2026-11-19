"""事件推送中心：ContextVar × WebSocket 直连（结构借鉴 deep_search_pro 的 ToolMonitor + ConnectionManager）。

与原深度项目保持一致的地方（这些是它设计得好的部分）：
1. **统一消息信封**：{type, event, message, data, session_id, timestamp}
   ——原项目 `message` 是给人看的现成文案，前端不用自己拼；本项目额外带上 session_id 便于排查多会话串台。
2. **双路径发送**：先在当前上下文探测事件循环，
   - 同一循环（工具在协程里被 await）→ `create_task`，零线程调度开销；
   - 跨线程（同步工具跑在线程池）→ `run_coroutine_threadsafe`，线程安全。
   原项目这段判断是对的；无脑统一用 run_coroutine_threadsafe 会慢并且丢失异常感知。
3. **单例 ToolMonitor + ConnectionManager**：连接注册表与循环绑定收口在一个类里，
   `manager.set_loop()` 只绑一次，工具侧只依赖 `monitor.xxx()`。
4. **脚本模式兜底**：无 WebSocket 时写 `builtins.runtime.stream_writer`，
   直接 `python main.py` 单跑 agent 也能看到事件流。

相对原项目的修正：
- `print` 调试全换成 loguru（去掉 `对比是不是同一个event_loop` 这类噪声，异常带上下文）；
- 推送失败不再静默：记录 warning 并计数（`manager.dropped`），排查断线丢事件有据可查；
- `send_to_thread` 找不到连接时告警而不是静默 return；
- 事件名沿用原项目的 tool_start / assistant_call / task_result / session_created，
  另加本项目需要的 pipeline_progress / error / done / interrupted。
"""
import asyncio
import builtins
import os
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Optional

from fastapi import WebSocket
from loguru import logger

from core.context import get_session_id

# 控制台回显（脚本单跑时想直接看事件流：set MONITOR_ECHO=1）
# 惰性读取：import 时 .env 可能还没被 core.config 加载
def _echo_enabled() -> bool:
    return os.getenv("MONITOR_ECHO", "0").lower() in ("1", "true", "yes")


def _log_send_error(fut):
    """统一记录推送异常：asyncio.Task 与 concurrent.futures.Future 都适用。"""
    try:
        exc = fut.exception()
    except BaseException:  # 已取消
        return
    if exc:
        logger.warning(f"[monitor] 事件推送失败：{type(exc).__name__}: {exc}")


class ConnectionManager:
    """session_id → WebSocket 注册表 + 绑定的事件循环。"""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None  # 由 WS 端点显式绑定
        self.dropped: int = 0

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """显式绑定 FastAPI 的事件循环（延迟绑定，避免 import 期无 loop）。"""
        self.loop = loop
        monitor.manager = self
        logger.debug(f"[monitor] ConnectionManager 绑定 loop={id(loop)}")

    async def connect(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        logger.info(f"[monitor] 客户端已连接：{session_id}")

    def disconnect(self, session_id: str):
        self.active_connections.pop(session_id, None)
        logger.info(f"[monitor] 客户端已断开：{session_id}")

    def has_connection(self, session_id: str) -> bool:
        return session_id in self.active_connections

    async def send_to_session(self, payload: dict, session_id: str):
        websocket = self.active_connections.get(session_id)
        if websocket is None:
            self.dropped += 1
            logger.warning(f"[monitor] 会话 {session_id} 无活跃连接，丢弃事件 {payload.get('event')}")
            return
        await websocket.send_json(payload)

    async def broadcast(self, payload: dict):
        for sid in list(self.active_connections):
            await self.send_to_session(payload, sid)


def build_event(event: str, message: str, data: Optional[dict] = None, session_id: str = "") -> dict:
    """手工构造与推送一致的消息信封（用于 WS 端点内的协议级回执，如参数错误）。"""
    return {
        "type": "monitor_event",
        "event": event,
        "message": message,
        "session_id": session_id or get_session_id() or "",
        "data": data or {},
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }


class ToolMonitor:
    """单例事件入口：ContextVar 定位会话 → 直推 WebSocket（无连接则走脚本模式）。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.manager = None
        return cls._instance

    # ---- 核心发送 ----
    def _emit(self, event: str, message: str, data: Optional[dict[str, Any]] = None):
        payload = build_event(event, message, data)
        self._to_websocket(payload)
        self._to_runtime(payload)
        if _echo_enabled():
            logger.info(f"[monitor:{event}] {message}")

    def _to_websocket(self, payload: dict):
        manager = self.manager
        if manager is None:
            return  # 脚本模式：没有 WS 兜底
        sid = payload["session_id"]
        if not sid or not manager.has_connection(sid):
            manager.dropped += 1
            logger.warning(f"[monitor] 无可用连接（session={sid or '空'}），事件 {payload['event']} 未推送")
            return

        loop = manager.loop
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        try:
            if loop is not None and current_loop is loop:
                # 同循环：直接起任务，免去线程安全调度的开销
                current_loop.create_task(manager.send_to_session(payload, sid)).add_done_callback(_log_send_error)
            elif loop is not None and loop.is_running():
                # 跨线程（同步工具跑在线程池）：线程安全方式投递回 WS 所在循环
                asyncio.run_coroutine_threadsafe(manager.send_to_session(payload, sid), loop).add_done_callback(_log_send_error)
            else:
                manager.dropped += 1
                logger.warning(f"[monitor] 事件循环不可用（session={sid}），丢弃事件 {payload['event']}")
        except Exception as e:  # 推送失败不能影响业务主流程
            manager.dropped += 1
            logger.warning(f"[monitor] 推送异常（session={sid}）：{e}")

    @staticmethod
    def _to_runtime(payload: dict):
        """脚本模式兜底：DeepAgents MockRuntime 会把事件写进 stream。"""
        runtime = getattr(builtins, "runtime", None)
        writer = getattr(runtime, "stream_writer", None)
        if writer:
            try:
                writer(payload)
            except Exception as e:
                logger.debug(f"[monitor] runtime.stream_writer 写入失败：{e}")

    # ---- 对外事件（与深度项目命名一致）----
    def report_session_dir(self, path: str):
        self._emit("session_created", f"工作目录已创建: {path}", {"path": path})

    def report_assistant(self, assistant_name: str, args: Optional[dict] = None):
        self._emit("assistant_call", f"正在调用助手: {assistant_name}", {"assistant_name": assistant_name, "args": args or {}})

    def report_tool(self, tool_name: str, args: Optional[dict] = None):
        self._emit("tool_start", f"开始执行工具: {tool_name}", {"tool_name": tool_name, "args": args or {}})

    def report_tool_end(self, tool_name: str, summary: str = ""):
        self._emit("tool_end", f"工具执行完成: {tool_name}", {"tool_name": tool_name, "summary": summary})

    def report_task_result(self, result: str):
        self._emit("task_result", "任务执行完成", {"result": result})

    # ---- 本项目扩展事件 ----
    def report_pipeline(self, stage: str, detail: str):
        """入库管线进度（pdf_to_md → md_img → upload_ragflow）。"""
        self._emit("pipeline_progress", detail, {"stage": stage, "detail": detail})

    def report_error(self, message: str):
        self._emit("error", message, {})

    def report_done(self, data: Optional[dict] = None):
        self._emit("done", "本轮任务结束", data or {})

    def report_interrupted(self, message: str = "任务已中断，状态已保存，可发送 resume 继续执行"):
        self._emit("interrupted", message, {})


# 全局单例
monitor = ToolMonitor()      # monitor.manager 为 None 时表示脚本模式（无 WS）
manager = ConnectionManager()
