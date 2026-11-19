from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from omegaconf import OmegaConf

load_dotenv()

PROJECT_ROOT = Path(__file__).parents[1].resolve()

_cfg = OmegaConf.load(PROJECT_ROOT / "conf" / "config.yaml")
# 允许 local.yaml 覆盖（不入库）
_local = PROJECT_ROOT / "conf" / "local.yaml"
if _local.exists():
    _cfg = OmegaConf.merge(_cfg, OmegaConf.load(_local))

cfg = OmegaConf.to_container(_cfg, resolve=True)
logger.remove()
logger.add(sink=lambda msg: print(msg, end=""), level="INFO")
logger.add(PROJECT_ROOT / "logs" / "app_{time:YYYY-MM-DD}.log", rotation="1 day", retention="14 days", encoding="utf-8")
