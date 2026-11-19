"""裁剪版用户资料入库管线（掌柜项目融合，计划书 3.2）：

上传 pdf/md → pdf转md(MinerU) → 图片处理(视觉模型转写+MinIO存图回填) → 上传 RAGFlow 会话 dataset
原 Milvus 分块/向量化链路已整体裁剪（决策3）。

说明：pdf_to_md 与 md_img 的具体实现通过 vendor 适配层调用掌柜代码，
未 vendored 时抛出明确错误（不静默）。
"""
import re
import shutil
from pathlib import Path

from core.config import PROJECT_ROOT, cfg
from api.monitor import monitor
from tools.ragflow_tools import upload_md_to_session_dataset


def _vendor(module: str, attr: str):
    try:
        mod = __import__(f"vendor.zhanggui.{module}", fromlist=[attr])
        return getattr(mod, attr)
    except ImportError as e:
        raise RuntimeError(
            f"入库管线依赖掌柜代码（vendor/zhanggui/{module}）未就绪：请先运行 `python scripts/vendor_projects.py`。原始错误：{e}"
        ) from e


async def run_import_pipeline(session_id: str, file_path: Path) -> dict:
    """主流程：任何异常都会向上抛出，由 API 层推送给前端。"""
    ext = file_path.suffix.lower()
    if ext not in cfg["upload"]["allowed_ext"]:
        raise ValueError(f"不支持的文件类型 {ext}，仅允许 {cfg['upload']['allowed_ext']}")

    session_dir = PROJECT_ROOT / cfg["paths"]["output_dir"] / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # 1. PDF → MD（MinerU；md 直接跳过）
    if ext == ".pdf":
        monitor.report_pipeline("pdf_to_md", f"开始解析 {file_path.name}")
        node_pdf_to_md = _vendor("app.import_process.agent.nodes.node_pdf_to_md", "node_pdf_to_md")
        state = node_pdf_to_md({"file_path": str(file_path), "output_dir": str(session_dir)})  # 适配点：按掌柜节点实际入参微调
        md_path = Path(state["md_path"]) if isinstance(state, dict) and "md_path" in state else session_dir / (file_path.stem + ".md")
        monitor.report_pipeline("pdf_to_md", f"完成：{md_path.name}")
    else:
        md_path = session_dir / file_path.name
        shutil.copy2(file_path, md_path)

    # 2. 图片处理：视觉模型转写 + MinIO 存图回填链接
    monitor.report_pipeline("md_img", "处理文档内图片（视觉模型转写 + MinIO 存图）")
    node_md_img = _vendor("app.import_process.agent.nodes.node_md_img", "node_md_img")
    state = node_md_img({"md_path": str(md_path)})  # 适配点：按掌柜节点实际入参微调
    monitor.report_pipeline("md_img", "图片处理完成")

    # 3. 上传 RAGFlow 会话 dataset（切割/向量化由 RAGFlow 完成）
    monitor.report_pipeline("upload_ragflow", f"上传 {md_path.name} 至会话资料库")
    result = upload_md_to_session_dataset(session_id, md_path, display_name=md_path.name)
    monitor.report_pipeline("upload_ragflow", "上传完成，资料已可在知识库助手中检索")

    return {"md_path": str(md_path), "ragflow": result}
