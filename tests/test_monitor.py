"""monitor 事件推送与日志可观测性的回归测试（pytest）。

覆盖四个测试友好点：
1. 统一消息信封结构
2. 内存事件缓冲（回看"刚才推了什么"）
3. dropped 计数读取接口（无连接时丢弃 +1）
4. session_id 注入日志 / LOG_LEVEL 调级

运行：uv run pytest tests/ -q
"""
import pytest

from api.monitor import build_event, manager, monitor


@pytest.fixture(autouse=True)
def _isolate_monitor():
    """每个用例前隔离单例状态，避免用例间互相污染。"""
    monitor.reset_stats()
    yield
    monitor.reset_stats()


def test_build_event_envelope():
    ev = build_event("tool_start", "开始执行工具: x", {"tool_name": "x"}, session_id="s1")
    assert ev["type"] == "monitor_event"
    assert ev["event"] == "tool_start"
    assert ev["message"] == "开始执行工具: x"
    assert ev["session_id"] == "s1"
    assert ev["data"] == {"tool_name": "x"}
    assert "timestamp" in ev


def test_event_history_records_and_orders():
    monitor.report_tool("t1")
    monitor.report_tool("t2")
    history = monitor.get_event_history(limit=10)
    assert [e["event"] for e in history[:2]] == ["tool_start", "tool_start"]
    assert history[0]["data"]["tool_name"] == "t2"  # 新的在前
    assert monitor.get_stats()["buffered"] == 2


def test_event_history_is_bounded():
    for i in range(monitor._HISTORY_MAX + 20):
        monitor.report_tool(f"t{i}")
    assert len(monitor.get_event_history(limit=10_000)) == monitor._HISTORY_MAX


def test_dropped_increments_without_connection(monkeypatch):
    # 强制构造"无连接"场景：manager 的 has_connection 恒为 False
    class _NoConn:
        dropped = 0
        active_connections = {}

        def has_connection(self, sid):
            return False

    monkeypatch.setattr(monitor, "manager", _NoConn())
    monitor.report_tool("no_conn")
    assert monitor.get_stats()["dropped"] == 1


def test_reset_stats_clears_state():
    monitor.report_tool("x")
    assert monitor.get_stats()["buffered"] > 0
    monitor.reset_stats()
    assert monitor.get_stats() == {"dropped": 0, "connections": 0, "buffered": 0}


def test_session_id_reachable_from_context():
    from core.context import get_session_id, set_thread_context

    token = set_thread_context("session-test")
    try:
        assert get_session_id() == "session-test"
    finally:
        from core.context import session_id_var

        session_id_var.reset(token)
