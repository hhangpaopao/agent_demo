"""用户资料入库管线（计划书 3.2）：
上传 pdf/md → pdf转md(MinerU) → 图片处理(VLM转写+MinIO回填) → 文档摘要(LLM) → 上传 RAGFlow 会话 dataset

原 Milvus 分块/向量化链路已整体裁剪（决策3）。
节点实现全部在本项目 import_pipeline/nodes/ 下，不再依赖 vendor 动态加载。
"""
import shutil
from pathlib import Path

from core.config import PROJECT_ROOT, cfg
from api.monitor import monitor
from tools.ragflow_tools import upload_md_to_session_dataset


async def run_import_pipeline(session_id: str, file_path: Path) -> dict:
    """主流程：任何异常都会向上抛出，由 API 层推送给前端。"""
    ext = file_path.suffix.lower()
    if ext not in cfg["upload"]["allowed_ext"]:
        raise ValueError(f"不支持的文件类型 {ext}，仅允许 {cfg['upload']['allowed_ext']}")

    session_dir = PROJECT_ROOT / cfg["paths"]["output_dir"] / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # 1. PDF → MD（MinerU；md 直接跳过）
    if ext == ".pdf":
        from import_pipeline.nodes.node_pdf_to_md import node_pdf_to_md

        monitor.report_pipeline("pdf_to_md", f"开始解析 {file_path.name}")
        state = node_pdf_to_md({"file_path": str(file_path), "output_dir": str(session_dir)})
        md_path = Path(state["md_path"])
        monitor.report_pipeline("pdf_to_md", f"完成：{md_path.name}")
    else:
        md_path = session_dir / file_path.name
        shutil.copy2(file_path, md_path)

    # 2. 图片处理：VLM 转写 + MinIO 存图回填（结果写回 md_path）
    from import_pipeline.nodes.node_md_img import node_md_img

    monitor.report_pipeline("md_img", "处理文档内图片（VLM 转写 + MinIO 存图）")
    node_md_img({"md_path": str(md_path)})
    monitor.report_pipeline("md_img", "图片处理完成")

    # 3. 文档摘要：LLM 生成结构化摘要，前置写入 md（随文档一起进 RAGFlow）
    from import_pipeline.nodes.node_document_summary import node_document_summary

    monitor.report_pipeline("document_summary", "生成文档摘要与元数据")
    state = node_document_summary({"md_path": str(md_path)})
    summary = state.get("summary", "")
    monitor.report_pipeline("document_summary", "摘要完成" if summary else "摘要跳过（不影响入库）")

    # 4. 上传 RAGFlow 会话 dataset（切割/向量化由 RAGFlow 完成）
    monitor.report_pipeline("upload_ragflow", f"上传 {md_path.name} 至会话资料库")
    result = upload_md_to_session_dataset(session_id, md_path, display_name=md_path.name)
    monitor.report_pipeline("upload_ragflow", "上传完成，资料已可在知识库助手中检索")

    return {"md_path": str(md_path), "ragflow": result, "summary": summary}
