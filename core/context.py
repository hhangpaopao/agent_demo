import re
from contextvars import ContextVar

# session_id 白名单：只允许字母、数字、下划线、短横线，长度 1-64。
# - 排除 . / \ 等路径字符，堵住 f"session_{id}" 目录穿越；
# - 排除空格与特殊字符，避免拼进 RAGFlow dataset 名时越权/注入；
# - 允许 -（兼容 UUID 格式），长度覆盖 UUID(36) 与常见短业务 ID。
_SESSION_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


def is_valid_session_id(session_id: str) -> bool:
    """校验外部传入的 session_id 是否安全（防止路径穿越 / dataset 越权）。"""
    return isinstance(session_id, str) and bool(_SESSION_ID_RE.fullmatch(session_id))


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


def reset_session_context(dir_token=None, id_token=None):
    """恢复 set_session_context / set_thread_context 之前的值。

    api/server.py 在 _run_agent / upload 的 finally 里以
    ``reset_session_context(*tokens)`` 成对传入两个 ContextVar.Token。
    清理路径上忽略无效 / 已用过 / 跨 context 的 token，避免 finally 抛错
    掩盖主流程异常（例如任务被 cancel 后重复 reset）。
    """
    for var, token in ((session_dir_var, dir_token), (session_id_var, id_token)):
        if token is None:
            continue
        try:
            var.reset(token)
        except (ValueError, RuntimeError):
            # token 已被 reset，或创建于另一个 context —— 清理阶段静默跳过
            pass
