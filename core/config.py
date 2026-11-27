import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from omegaconf import OmegaConf

from core.context import get_session_id

load_dotenv()

PROJECT_ROOT = Path(__file__).parents[1].resolve()

_cfg = OmegaConf.load(PROJECT_ROOT / "conf" / "config.yaml")
# 允许 local.yaml 覆盖（不入库）
_local = PROJECT_ROOT / "conf" / "local.yaml"
if _local.exists():
    _cfg = OmegaConf.merge(_cfg, OmegaConf.load(_local))

cfg = OmegaConf.to_container(_cfg, resolve=True)

# 日志级别：环境变量优先（容器部署 -e LOG_LEVEL=DEBUG 即可临时调级），非法值回退 INFO
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if _LOG_LEVEL not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
    _LOG_LEVEL = "INFO"


# 每条日志注入 session_id：多会话并发时可据此过滤定位
# 无会话上下文（如 startup/shutdown）填 "-"，不影响日志发出
def _inject_session(record):
    record["extra"]["session_id"] = get_session_id() or "-"


_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
    "session={extra[session_id]} | {name}:{function}:{line} - {message}"
)

logger.remove()
logger.configure(patcher=_inject_session)
logger.add(sink=lambda msg: print(msg, end=""), level=_LOG_LEVEL, format=_LOG_FORMAT)
logger.add(
    PROJECT_ROOT / "logs" / "app_{time:YYYY-MM-DD}.log",
    level=_LOG_LEVEL,
    rotation="1 day",
    retention="14 days",
    encoding="utf-8",
    format=_LOG_FORMAT,
)
logger.debug(f"[config] 日志初始化完成：level={_LOG_LEVEL}, root={PROJECT_ROOT}")
