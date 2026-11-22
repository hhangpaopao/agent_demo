"""入库管线节点异常：统一异常类型，便于 API 层区分失败阶段并推送给用户。"""


class PipelineNodeError(Exception):
    """节点异常基类。"""

    def __init__(self, message: str, node_name: str = ""):
        self.node_name = node_name
        super().__init__(f"[{node_name}] {message}" if node_name else message)


class ValidationError(PipelineNodeError):
    """输入参数校验失败。"""


class FileProcessingError(PipelineNodeError):
    """文件读写失败。"""


class PdfConversionError(PipelineNodeError):
    """MinerU PDF 转换失败。"""


class ImageProcessingError(PipelineNodeError):
    """图片转写 / MinIO 上传失败。"""
