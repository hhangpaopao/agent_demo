"""把三个既有项目的代码 vendored 进 deep_pro（合并仓库）。

用法：python scripts/vendor_projects.py [源根目录]
默认源根目录：C:/Users/35448/Desktop/新建文件夹

- deep_search_pro ：骨架已直接重写进本项目，仅复制 utils/word_converter.py（若缺失）
- WenShu_python   ：复制 app/（问数 NL2SQL 图）→ vendor/wenshu/app
- zhanggui_python ：复制 MinerU/MinIO 相关节点与客户端 → vendor/zhanggui/...
"""
import shutil
import sys
from pathlib import Path

EXCLUDE = {"__pycache__", "logs", "output", "updated", ".venv", "node_modules", "笔记图片"}


def copy_tree(src: Path, dst: Path):
    if not src.exists():
        print(f"[跳过] 源不存在：{src}")
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if any(part in EXCLUDE for part in item.parts):
            continue
        if item.is_file() and item.suffix == ".py":
            rel = item.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    print(f"[完成] {src} → {dst}")


_WENSHU_CONFIG_YML = """\
# 自动生成，勿手改（由 scripts/vendor_projects.py 从 .env 变量渲染）
# 问数原项目 app/conf/app_config.py 会读取 <vendor>/wenshu/conf/app_config.yml
logging:
  file:
    enable: false            # 统一走本项目 loguru，不再单独写文件
    level: INFO
    path: logs
    rotation: "10 MB"
    retention: "7 days"
  console:
    enable: true
    level: INFO

db_meta:
  host: ${oc.env:META_MYSQL_HOST,localhost}
  port: ${oc.env:META_MYSQL_PORT,3307}
  user: ${oc.env:META_MYSQL_USER,root}
  password: ${oc.env:META_MYSQL_PASSWORD,}
  database: ${oc.env:META_MYSQL_DATABASE,wenshu_meta}

db_dw:
  host: ${oc.env:DW_MYSQL_HOST,localhost}
  port: ${oc.env:DW_MYSQL_PORT,3306}
  user: ${oc.env:DW_MYSQL_USER,readonly_user}
  password: ${oc.env:DW_MYSQL_PASSWORD,}
  database: ${oc.env:DW_MYSQL_DATABASE,biz_dw}

qdrant:
  host: ${oc.env:QDRANT_HOST,localhost}
  port: ${oc.env:QDRANT_PORT,6333}
  embedding_size: ${oc.env:EMBEDDING_DIM,1024}

embedding:
  host: ${oc.env:EMBEDDING_HOST,localhost}
  port: ${oc.env:EMBEDDING_PORT,8081}
  model: ${oc.env:EMBEDDING_MODEL,BAAI/bge-large-zh-v1.5}

es:
  host: ${oc.env:ES_HOST,localhost}
  port: ${oc.env:ES_PORT,9200}
  index_name: data_agent

llm:
  model_name: ${oc.env:LLM_MODEL,gpt-4o}
  api_key: ${oc.env:LLM_API_KEY,}
  base_url: ${oc.env:LLM_BASE_URL,https://api.openai.com/v1}
"""


def write_wenshu_config(vendor: Path) -> None:
    """生成问数图所需的 app_config.yml（用 oc.env 解析器，配置真源仍是根目录 .env）。"""
    target = vendor / "wenshu" / "conf" / "app_config.yml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_WENSHU_CONFIG_YML, encoding="utf-8")
    print(f"[完成] 生成问数配置模板 → {target}")


def main(source_root: Path):
    project_root = Path(__file__).parents[1]
    vendor = project_root / "vendor"

    # 1) 问数：整包 app（NL2SQL 图 + 客户端 + 仓储层）
    copy_tree(source_root / "WenShu_python" / "app", vendor / "wenshu" / "app")
    for f in ["main.py"]:
        src = source_root / "WenShu_python" / f
        if src.exists():
            shutil.copy2(src, vendor / "wenshu" / f)
    write_wenshu_config(vendor)  # 生成 vendor/wenshu/conf/app_config.yml（否则图导入即失败）

    # 2) 掌柜：入库管线所需的最小集合（MinerU 节点 / MinIO / 配置 / core）
    zg_src = source_root / "zhanggui_python" / "app"
    zg_dst = vendor / "zhanggui" / "app"
    for sub in ["import_process/agent/nodes/node_pdf_to_md.py",
                "import_process/agent/nodes/node_md_img.py",
                "import_process/agent/nodes/node_entry.py",
                "clients/minio_utils.py", "clients/mongo_history_utils.py",
                "conf/mineru_config.py", "conf/minio_config.py",
                "core/load_prompt.py", "core/logger.py",
                # 注意：只取 lm_utils（视觉/对话模型调用）——不取 embedding_utils，
                # 它依赖 FlagEmbedding/pymilvus，属决策3 已裁剪的 Milvus 链路
                "lm/lm_utils.py",
                "utils/path_util.py"]:
        s = zg_src / sub
        if s.exists():
            t = zg_dst / sub
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, t)
    print(f"[完成] 掌柜最小集合 → {zg_dst}")

    # 3) deep_search_pro：word_converter（若本项目缺失）
    wc_src = source_root / "deep_search_pro" / "utils" / "word_converter.py"
    wc_dst = project_root / "utils" / "word_converter.py"
    if wc_src.exists() and not wc_dst.exists():
        wc_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wc_src, wc_dst)
        print(f"[完成] {wc_src} → {wc_dst}")

    # 4) vendor 包标识文件
    for pkg in [vendor, vendor / "wenshu", vendor / "wenshu" / "app", vendor / "zhanggui", vendor / "zhanggui" / "app"]:
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").touch()
    print("\n vendored 完成。下一步：")
    print(" 1. 复制 .env.example 为 .env 并填写配置")
    print(" 2. 按 vendor/wenshu/app 内的相对导入情况微调（原项目以 app 为根）")
    print(" 3. uv sync && uvicorn api.server:app")


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("C:/Users/35448/Desktop/新建文件夹")
    main(root)
