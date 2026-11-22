"""节点：PDF → Markdown（MinerU）。

从 shopkeeper_brain/knowledge/processor/import_process/nodes/pdf_to_md_node.py 移植，
按本项目约定改造：
- 类方法 process(state) → 函数 node_pdf_to_md(state)，与 pipeline.py 调用方式一致
- BaseNode/self.logger → loguru；自定义异常统一到本目录 exceptions.py
- 去掉 knowledge.* 包依赖（本项目不内嵌 shopkeeper 包结构）

输入 state: {"file_path": str, "output_dir": str}
输出 state: 原样 + {"md_path": str}
"""
import subprocess
import time
from pathlib import Path

from loguru import logger

from import_pipeline.nodes.exceptions import (
    FileProcessingError,
    PdfConversionError,
    ValidationError,
)


def node_pdf_to_md(state: dict) -> dict:
    """用 MinerU 把 PDF 解析成 MD，返回 md_path。"""
    import_file_path, file_dir_path = _validate_inputs(state)
    code = _execute_mineru(import_file_path, file_dir_path)
    if code != 0:
        raise PdfConversionError(
            f"MinerU 解析 PDF 失败（退出码 {code}）。请确认已安装 mineru 且模型可用：pip install 'mineru[all]'",
            node_name="pdf_to_md",
        )
    state["md_path"] = _get_md_path(import_file_path, file_dir_path)
    return state


def _validate_inputs(state: dict) -> tuple[Path, Path]:
    import_file_path = state.get("file_path", "")
    if not import_file_path:
        raise ValidationError("待解析的文件路径为空", node_name="pdf_to_md")
    path = Path(import_file_path)
    if not path.exists():
        raise FileProcessingError(f"文件路径不存在：{import_file_path}", node_name="pdf_to_md")

    file_dir = state.get("output_dir", "") or str(path.parent)
    logger.info(f"[pdf_to_md] 输入={import_file_path} 输出目录={file_dir}")
    return path, Path(file_dir)


def _execute_mineru(import_file_path: Path, file_dir_path: Path) -> int:
    """子进程跑 mineru CLI，实时读日志行（bufsize=1），返回退出码。"""
    cmd = ["mineru", "-p", str(import_file_path), "-o", str(file_dir_path), "--source", "local"]
    logger.info(f"[pdf_to_md] 执行命令：{' '.join(cmd)}")
    start = time.time()
    try:
        proc = subprocess.Popen(
            args=cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            errors="replace",
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
    except FileNotFoundError as e:
        raise PdfConversionError(
            "未找到 mineru 命令。请先安装：pip install 'mineru[all]'（或按官方源码安装）",
            node_name="pdf_to_md",
        ) from e

    for line in proc.stdout:
        line = line.rstrip()
        if line:
            logger.debug(f"[mineru] {line}")
    code = proc.wait()
    logger.info(f"[pdf_to_md] MinerU 结束，退出码={code}，耗时={time.time() - start:.2f}s")
    return code


def _get_md_path(import_file_path: Path, file_dir_path: Path) -> str:
    """MinerU 产物：<out>/<stem>/hybrid_auto/<stem>.md；找不到时递归兜底。"""
    stem = import_file_path.stem
    expected = file_dir_path / stem / "hybrid_auto" / f"{stem}.md"
    if expected.exists():
        return str(expected)

    candidates = [p for p in file_dir_path.rglob(f"{stem}.md") if p.is_file()]
    if candidates:
        # 优先 hybrid_auto 目录，其次取最新修改
        candidates.sort(key=lambda p: ("hybrid_auto" in str(p), p.stat().st_mtime), reverse=True)
        logger.warning(f"[pdf_to_md] 未找到预期路径 {expected}，使用兜底 {candidates[0]}")
        return str(candidates[0])

    raise FileProcessingError(
        f"MinerU 执行成功但未产出 md 文件（预期 {expected}）", node_name="pdf_to_md"
    )
