"""提示词加载器：prompts/ 目录 + 按名字取用 + 变量渲染。

设计要点（与 zhanggui 的 load_prompt 保持一致的心智，但避开它的坑）：

1. **目录化**：提示词不再写死在 .py 里，放到 `prompts/{name}.md`。
   改文案不用碰代码，也便于做 A/B 或按语言分目录。
2. **按名字判断**：`load_prompt("db")` 支持短别名（见 ALIASES），
   也会自动尝试 .md / .prompt / .txt 三种后缀；找不到时把可用名字列出来，别让人猜。
3. **渲染用 string.Template 而不是 str.format**：
   提示词里常有 SQL 模板 `select * from {table_name} limit 10`、JSON 示例、YAML 花括号，
   用 `.format()` 会把 `{table_name}` 当变量求值直接抛 KeyError（曾踩过）。
   `Template` 只认 `${var}`，单个花括号原样保留。
4. **原文缓存**：磁盘读取走 lru_cache，渲染每次现算（同一个模板可能带不同参数）。
"""
from functools import lru_cache
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

# 短名 → 实际文件名（不带后缀）。加新 agent 时在这里登记一行。
ALIASES = {
    "main": "main_agent",
    "search": "network_search_agent",
    "db": "database_query_agent",
    "kb": "knowledge_base_agent",
}

_SUFFIXES = (".md", ".prompt", ".txt")


def _resolve(name: str) -> Path:
    """把 name/别名解析成真实文件路径；找不到时抛出带候选清单的错误。"""
    stem = ALIASES.get(name, name)
    stem_path = PROMPT_DIR / stem
    if stem_path.suffix in _SUFFIXES and stem_path.is_file():
        return stem_path
    for suffix in _SUFFIXES:
        candidate = PROMPT_DIR / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    available = ", ".join(list_prompts()) or "（目录为空）"
    raise FileNotFoundError(f"提示词不存在：{name}（解析为 {stem}），可用：{available}")


@lru_cache(maxsize=None)
def _read(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8").strip() + "\n"


def list_prompts() -> list[str]:
    """列出 prompts/ 下所有可用名字（不含后缀），便于排错与自查。"""
    if not PROMPT_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROMPT_DIR.iterdir() if p.suffix in _SUFFIXES)


def load_prompt(name: str, **kwargs) -> str:
    """按名字加载提示词并渲染 ${var} 占位符。

    :param name: 提示词名或短别名（main / search / db / kb）
    :param kwargs: 需渲染的变量，键名与模板里的 ${key} 对应
    :return: 渲染后的提示词文本

    示例：
        load_prompt("db", max_attempts=3)
        load_prompt("main_agent")
    """
    raw = _read(str(_resolve(name)))

    if not kwargs:
        # 没传参也不能放任 ${var} 原样交给模型：先判断模板里是否还有占位符
        missing = sorted(Template(raw).get_identifiers())
        if missing:
            raise KeyError(f"提示词 {name} 需要变量 {missing}，但调用时未提供")
        return raw  # 无占位符：直接返回，模板里的 $ 也不会被误处理

    try:
        return Template(raw).substitute(**kwargs)
    except KeyError as e:
        raise KeyError(
            f"提示词 {name} 渲染缺少变量 {e}；已提供：{sorted(kwargs)}"
        ) from e
    except ValueError as e:  # 模板里出现孤立的 $ 符号
        raise ValueError(f"提示词 {name} 存在非法的 $ 占位符（如需字面量请写 $$）：{e}") from e


def reload_prompts():
    """清空原文缓存（本地调试时改完 md 立即生效，无需重启进程）。"""
    _read.cache_clear()
