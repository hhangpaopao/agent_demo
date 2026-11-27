"""一键生成元数据知识库。

把 vendor/wenshu 归档的 build_meta_knowledge.py 包一层，解决两个坑：
1. 归档配置 app_config.yml 用 ${oc.env:VAR} 读操作系统环境变量，不读 .env —— 这里先 load_dotenv 注入
2. 归档脚本用 from app.xxx 绝对导入，直接跑找不到模块 —— 这里把 vendor/wenshu 插入 sys.path

用法：
    .venv/Scripts/python.exe run_build_meta.py

前置：云端 TEI(8081) / Qdrant(6333) / ES(9200) 必须可达，且 ES 装了 ik 分词插件。
"""
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")  # 把 .env 里的云端连接参数注入 os.environ

WENSHU = ROOT / "vendor" / "wenshu"
sys.path.insert(0, str(WENSHU))

from app.scripts.build_meta_knowledge import build  # noqa: E402

META_CONFIG = WENSHU / "conf" / "meta_config.yml"


if __name__ == "__main__":
    print(f"[meta] 描述文件: {META_CONFIG}")
    asyncio.run(build(str(META_CONFIG)))
    print("[meta] 完成")
