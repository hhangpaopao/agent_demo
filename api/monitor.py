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
import json
import os
import threading
import time
from collections import deque
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from loguru import logger

from core.context import get_session_dir, get_session_id

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


class StuckDetector:
    """借鉴 OpenHands StuckDetector：滑动窗口检测重复动作，防止无限循环。

    只发告警、不强制打断 agent——避免误杀正常的多轮迭代（如 NL2SQL 3 次尝试）。
    按 session 隔离，避免多会话串台。
    """

    WINDOW = 12            # 观察最近 12 个 tool_start 事件
    THRESHOLD = 4          # 同一 (工具名+参数签名) 在窗口内出现 >=4 次 → 告警
    ALERT_COOLDOWN = 5     # 同一签名告警后，冷却 5 次调用再告警，避免刷屏

    ALERT_LIMIT = 3            # 本轮告警累计 >=3 次 → 主 agent 注入收尾指令（防空转）

    def __init__(self):
        self._events: dict[str, deque] = {}       # sid → deque of (tool, sig)
        self._last_alert: dict[str, dict[str, int]] = {}  # sid → {sig: 当时的调用计数}
        self._alert_total: dict[str, int] = {}    # sid → 本轮告警累计次数（供 agent 读）
        self._nudged_sessions: set[str] = set()   # 已注入收尾指令的会话（随 reset 清空）

    def get_alert_count(self, sid: str) -> int:
        """返回本会话累计告警次数，供主 agent 判断是否需要收尾。"""
        return self._alert_total.get(sid, 0)

    def mark_nudged(self, sid: str) -> bool:
        """标记会话已注入收尾指令；返回 True 表示本次首次注入。"""
        if sid in self._nudged_sessions:
            return False
        self._nudged_sessions.add(sid)
        return True

    def reset(self, sid: str) -> None:
        """每轮任务开始时重置该会话的检测与告警计数。"""
        self._events.pop(sid, None)
        self._last_alert.pop(sid, None)
        self._alert_total.pop(sid, None)
        self._nudged_sessions.discard(sid)

    def observe(self, sid: str, tool: str, args: dict) -> str | None:
        """记录一次 tool_start，返回告警文案（若判定卡死），否则 None。

        只判定「同工具 + 同参数」重复（对齐 OpenHands StuckDetector：它的五种模式
        全部基于同一动作比较，从不因"同工具换参数多次调用"判卡死——那是正常探索）。

        次数上限不归这里管：烧钱工具都已有硬上限（知识库/数据库 16、主图 10、
        联网 5、RAGFlow 提问 8），早期预警若按工具条数触发，会把"5 轮×2 张取图"
        这类合法高频误判为卡死，进而注入收尾指令掐断采集。
        """
        if not sid:
            return None
        sig = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)[:200]
        window = self._events.setdefault(sid, deque(maxlen=self.WINDOW))
        window.append((tool, sig))

        count = sum(1 for t, s in window if t == tool and s == sig)
        last = self._last_alert.setdefault(sid, {})
        if count >= self.THRESHOLD:
            prev_count = last.get(sig, -self.ALERT_COOLDOWN)
            if not (prev_count >= 0 and count - prev_count < self.ALERT_COOLDOWN):
                last[sig] = count
                self._alert_total[sid] = self._alert_total.get(sid, 0) + 1
                return f"疑似重复动作：工具「{tool}」在最近 {len(window)} 步内以相同参数调用 {count} 次，建议核查是否卡死"
        return None


class StuckNudgeMiddleware(AgentMiddleware):
    """本会话卡死告警累计达阈值时，注入一条强指令引导 agent 用现有证据收尾。

    只注入一次（ContextVar 标记），避免每轮模型调用重复堆消息。挂在采集类子 agent 上——
    空转发生在子 agent 内部，指令必须进它的模型请求才有效。
    """
    name = "stuck_nudge"

    def __init__(self, alert_limit: int = 3):
        self.alert_limit = alert_limit

    async def awrap_model_call(self, request, handler):
        sid = get_session_id()
        if sid and monitor.get_stuck_count(sid) >= self.alert_limit and monitor.mark_nudged(sid):
            nudge = HumanMessage(
                content=(
                    "【系统硬指令·防空转】检测到本轮存在大量重复工具调用，已触发成本保护。"
                    "立即停止一切检索/查询，用已落盘 evidence 中现有证据直接撰写并落盘报告，"
                    "缺数据的部分如实标注【未采集】，不要再调用任何采集工具。"
                )
            )
            return await handler(request.override(messages=list(request.messages) + [nudge]))
        return await handler(request)


class ToolMonitor:
    """单例事件入口：ContextVar 定位会话 → 直推 WebSocket（无连接则走脚本模式）。"""

    _instance = None
    _HISTORY_MAX = 200  # 环形缓冲上限：足够排障回看，又不会无限增长

    _EVENTS_FILE = "events.jsonl"       # 会话事件落盘文件（append-only，审计/耗时分析用）
    _DISK_MAX_BYTES = 2 * 1024 * 1024   # 单会话事件文件上限 2MB，超限停止追加（防跑飞）

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.manager = None
            cls._instance._history = deque(maxlen=cls._HISTORY_MAX)
            cls._instance._detector = StuckDetector()
            cls._instance._disk_lock = threading.Lock()
        return cls._instance

    # ---- 核心发送 ----
    def _emit(self, event: str, message: str, data: Optional[dict[str, Any]] = None):
        payload = build_event(event, message, data)
        self._history.append(payload)   # 环形缓冲：测试/排障可回看最近事件
        self._to_disk(payload)          # 旁路落盘：失败不影响推送
        self._to_websocket(payload)
        self._to_runtime(payload)
        self._detect_stuck(payload, data)  # 重复动作检测（旁路，告警不打断）
        if _echo_enabled():
            logger.info(f"[monitor:{event}] {message}")

    def _to_disk(self, payload: dict):
        """事件 append-only 落盘到会话目录 events.jsonl。

        逐条 flush 保证进程被杀也不丢已发出的事件；带毫秒时间戳便于算耗时。
        会话目录由 ContextVar 提供（session_id 已在 WS 入口白名单校验）。
        """
        session_dir = get_session_dir()
        if not session_dir:
            return  # 脚本模式/无会话上下文：不落盘
        try:
            path = Path(session_dir) / self._EVENTS_FILE
            with self._disk_lock:
                if path.exists() and path.stat().st_size >= self._DISK_MAX_BYTES:
                    return  # 超限保护：静默停止追加，避免日志跑飞占满磁盘
                path.parent.mkdir(parents=True, exist_ok=True)
                record = dict(payload)
                record["ts_ms"] = int(time.time() * 1000)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    f.flush()
        except Exception as e:  # 落盘失败不能影响业务主流程
            logger.warning(f"[monitor] 事件落盘失败（不影响主流程）：{type(e).__name__}: {e}")

    def _detect_stuck(self, payload: dict, data: Optional[dict]):
        """对 tool_start 事件做重复检测；命中则发 stuck_detected 告警。"""
        if payload.get("event") != "tool_start":
            return
        sid = payload.get("session_id", "")
        tool = (data or {}).get("tool_name", "") or "未知工具"
        alert = self._detector.observe(sid, tool, (data or {}).get("args") or {})
        if alert:
            self._emit("stuck_detected", alert, {"tool_name": tool})

    def get_stuck_count(self, sid: str) -> int:
        """本会话累计卡死告警次数，供主 agent 判断是否需要收尾。"""
        return self._detector.get_alert_count(sid)

    def mark_nudged(self, sid: str) -> bool:
        """标记本会话已注入收尾指令；返回 True 表示首次注入。"""
        return self._detector.mark_nudged(sid)

    def reset_stuck(self, sid: str) -> None:
        """每轮任务开始时重置该会话的卡死检测状态。"""
        self._detector.reset(sid)

    # ---- 测试 / 排障接口 ----
    def get_event_history(self, limit: int = 50) -> list[dict]:
        """最近事件快照（新→旧），每条即完整信封。测试可断言推送了什么。"""
        return list(reversed(self._history))[:limit]

    def get_stats(self) -> dict:
        """推送健康度快照：丢弃数 / 活跃连接 / 缓冲条数。"""
        return {
            "dropped": self.manager.dropped if self.manager else 0,
            "connections": len(self.manager.active_connections) if self.manager else 0,
            "buffered": len(self._history),
        }

    def reset_stats(self):
        """清空缓冲与丢弃计数——测试 fixture 中调用，保证用例间隔离。"""
        self._history.clear()
        if self.manager:
            self.manager.dropped = 0

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
        """入库管线进度（pdf_to_md → md_img → document_summary → upload_ragflow）。"""
        self._emit("pipeline_progress", detail, {"stage": stage, "detail": detail})

    def report_upload(self, status: str, task_id: str, file_name: str = "", detail: str = ""):
        """上传任务状态（uploading / done / failed）。

        与 pipeline_progress 区分：这是任务级状态，前端据此显示"上传ing"常驻指示，
        解决"传完立刻提问撞上空检索"的竞态——用户能看到资料还在处理。
        """
        message = detail or {
            "uploading": f"资料上传中：{file_name}",
            "done": f"资料入库完成：{file_name}",
            "failed": f"资料入库失败：{file_name}",
        }.get(status, f"上传状态：{status}")
        self._emit(
            "upload_status",
            message,
            {"status": status, "task_id": task_id, "file": file_name, "detail": detail},
        )

    def report_error(self, message: str):
        self._emit("error", message, {})

    def report_done(self, data: Optional[dict] = None):
        self._emit("done", "本轮任务结束", data or {})

    def report_interrupted(self, message: str = "任务已中断，状态已保存，可发送 resume 继续执行"):
        self._emit("interrupted", message, {})


# 全局单例
monitor = ToolMonitor()      # monitor.manager 为 None 时表示脚本模式（无 WS）
manager = ConnectionManager()
