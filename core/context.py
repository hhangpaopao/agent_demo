from contextvars import ContextVar

# 会话上下文（沿用 deep_search_pro 的 ContextVar 方案，协程安全）
session_dir_var: ContextVar[str | None] = ContextVar("session_dir", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)


def set_session_context(session_dir: str):
    return session_dir_var.set(session_dir)


def set_thread_context(session_id: str):
    return session_id_var.set(session_id)


def get_session_dir() -> str:
    return session_dir_var.get() or ""


def get_session_id() -> str:
    return session_id_var.get() or ""
