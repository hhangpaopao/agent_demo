"""问数图（WenShu）执行器：把一张异步 LangGraph 图安全地跑在同步工具里。

为什么需要这一层：
1. 问数节点从 `runtime.context` 取 embedding_client / 各仓储实例，直接
   `graph.invoke({"query": ...})` 会因缺少 context 而报错；
2. 图内部全是异步客户端（AsyncQdrantClient / AsyncElasticsearch / asyncmy 连接池），
   而 deepagents 的工具是同步函数，跑在线程池里；
3. async 连接池绑定事件循环，**不能用 asyncio.run 每次新建 loop**（第二次调用就会
   报 "attached to a different loop"）。

方案：起一个专用后台事件循环线程（常驻），客户端只在该 loop 上初始化一次，
每次查询用 run_coroutine_threadsafe 提交协程，天然串行、复用连接池。
"""
import asyncio
import sys
import threading
from typing import Any

from loguru import logger

from core.config import PROJECT_ROOT

_WENSHU_ROOT = PROJECT_ROOT / "vendor" / "wenshu"


class WenshuRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ready = False
        self._graph = None
        self._app_config = None

    # ---------------- 生命周期 ----------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop and self._loop.is_running():
            return self._loop
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="wenshu-graph-loop", daemon=True
            )
            thread.start()
            self._loop, self._thread = loop, thread
            logger.info("[wenshu] 后台事件循环已启动")
        return self._loop

    def ensure_ready(self) -> None:
        """在专用 loop 上初始化图与客户端（只做一次，线程安全）。"""
        if self._ready:
            return
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(self._ainit(), loop)
        fut.result(timeout=120)  # 首次建连接池 + 导入图，给足时间
        self._ready = True

    async def _ainit(self) -> None:
        if not _WENSHU_ROOT.exists():
            raise RuntimeError(
                f"问数代码未 vendored（缺少 {_WENSHU_ROOT}）。"
                "请先运行 `python scripts/vendor_projects.py`。"
            )
        # 问数原项目以 app 为根，用绝对导入（from app.xxx import ...）
        if str(_WENSHU_ROOT) not in sys.path:
            sys.path.insert(0, str(_WENSHU_ROOT))

        try:
            from app.agent.graph import graph
            from app.client.embedding_client_manage import embedding_client_manager
            from app.client.es_client_manage import es_client_manager
            from app.client.mysql_client_manage import (
                dw_mysql_client_manager,
                meta_mysql_client_manager,
            )
            from app.client.qdrant_client_manage import qdrant_client_manager
        except ImportError as e:  # 依赖缺失时给明确指引，而不是让主智能体看到一坨 traceback
            raise RuntimeError(
                f"问数图导入失败：{e}。请确认已 `uv add -r requirements.txt`，"
                "且 vendor/wenshu/conf/app_config.yml 已生成。"
            ) from e

        # 客户端必须在“将来真正使用它们的那个 loop”上初始化，否则连接池会绑定错循环
        qdrant_client_manager.init()
        es_client_manager.init()
        embedding_client_manager.init()
        meta_mysql_client_manager.init()
        dw_mysql_client_manager.init()

        self._graph = graph
        self._clients = {
            "qdrant": qdrant_client_manager,
            "es": es_client_manager,
            "meta": meta_mysql_client_manager,
            "dw": dw_mysql_client_manager,
        }
        logger.info("[wenshu] 图与客户端初始化完成（qdrant/es/embedding/mysql）")

    async def _aclose(self) -> None:
        clients = getattr(self, "_clients", None) or {}
        for name in ("qdrant", "es", "meta", "dw"):
            mgr = clients.get(name)
            if mgr is None:
                continue
            try:
                await mgr.close()
            except Exception as e:  # 关闭失败不影响进程退出
                logger.warning(f"[wenshu] 关闭 {name} 客户端失败：{e}")

    def shutdown(self) -> None:
        if not (self._loop and self._loop.is_running()):
            return
        try:
            asyncio.run_coroutine_threadsafe(self._aclose(), self._loop).result(timeout=15)
        except Exception as e:
            logger.warning(f"[wenshu] 关闭连接池异常：{e}")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._ready = False
        logger.info("[wenshu] 已关闭")

    # ---------------- 执行 ----------------
    async def _arun(self, question: str) -> dict[str, Any]:
        from app.agent.context import DataAgentContext
        from app.repositories.es.value_es_repository import ValueEsRepository
        from app.repositories.mysql.dw.dw_mysql_repository import DwMysqlRepository
        from app.repositories.mysql.meta.meta_mysql_repository import MetaMysqlRepository
        from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
        from app.repositories.qdrant.metric_qdrant_repository import MetricsRepository
        from app.client.embedding_client_manage import embedding_client_manager
        from app.client.es_client_manage import es_client_manager
        from app.client.mysql_client_manage import (
            dw_mysql_client_manager,
            meta_mysql_client_manager,
        )
        from app.client.qdrant_client_manage import qdrant_client_manager

        # 会话按请求创建（与原项目测试代码一致），连接池复用
        async with meta_mysql_client_manager.session_factoryy() as meta_session, \
                dw_mysql_client_manager.session_factoryy() as dw_session:
            ctx = DataAgentContext(
                column_qdrant_repository=ColumnQdrantRepository(qdrant_client_manager.client),
                metric_qdrant_repository=MetricsRepository(qdrant_client_manager.client),
                value_es_repository=ValueEsRepository(es_client_manager.client),
                meta_mysql_repository=MetaMysqlRepository(meta_session),
                dw_mysql_repository=DwMysqlRepository(dw_session),
                embedding_client=embedding_client_manager.client,
            )
            # error 必须显式给 None：validate_sql 的条件边据此判断是否需要修正
            state = {"query": question, "question": question, "error": None, "keywords": ""}
            return await self._graph.ainvoke(state, context=ctx)

    def run(self, question: str, timeout: float = 180.0) -> dict[str, Any]:
        self.ensure_ready()
        fut = asyncio.run_coroutine_threadsafe(self._arun(question), self._loop)
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            fut.cancel()
            raise TimeoutError(f"NL2SQL 执行超时（{timeout:.0f}s），问题：{question[:80]}")


_runner = WenshuRunner()


def run_wenshu(question: str, timeout: float = 180.0) -> dict[str, Any]:
    """同步入口：供 @tool 调用。"""
    return _runner.run(question, timeout=timeout)


def shutdown_wenshu() -> None:
    _runner.shutdown()
