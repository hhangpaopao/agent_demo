"""数据库直查工具：list_tables / preview / 只读 execute_sql_query。

注意：execute_sql_query 仅作为 nl2sql 的降级兜底（计划书决策1），已加只读校验。
"""
import re

from langchain_core.tools import tool
from mysql.connector import connect, Error

from api.monitor import monitor
from core.config import cfg

_READONLY_BLOCK = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|call|set|lock)\b", re.I)


def _dw_config() -> dict:
    d = cfg["db"]["dw"]
    return {
        "host": d["host"], "port": int(d["port"]), "user": d["user"],
        "password": d["password"], "database": d["database"], "charset": "utf8mb4",
        "autocommit": True,
    }


def _run_query(sql: str, limit: int = 100) -> str:
    try:
        with connect(**_dw_config()) as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                description = cursor.description
                if not description:
                    return "查询没有返回结果。"
                columns = [d[0] for d in description]
                rows = cursor.fetchmany(limit)
                lines = [",".join(map(str, r)) for r in rows]
                return ",".join(columns) + ("\n" + "\n".join(lines) if lines else "\n(无数据)")
    except Error as e:
        return f"查询出现异常：{e}"


@tool
def list_sql_tables() -> str:
    """查询业务库中所有可用的表名。在任何数据查询前先调用本工具确认表名。"""
    monitor.report_tool(tool_name="数据库表名查询：list_sql_tables", args={})
    return _run_query("show tables")


@tool
def get_table_data(table_name: str) -> str:
    """预览指定表的数据（至多100条，CSV格式：首行列名）。
    用于确认表结构与数据格式，为 nl2sql 改写或手写SQL提供 schema 上下文。
    :param table_name: 表名
    """
    monitor.report_tool(tool_name="数据库表预览：get_table_data", args={"table_name": table_name})
    if not re.fullmatch(r"[0-9a-zA-Z_$.]+", table_name):
        return "表名不合法，仅允许字母数字下划线。"
    return _run_query(f"select * from {table_name} limit 100")


@tool
def execute_sql_query(query: str) -> str:
    """执行自定义只读 SQL（仅 SELECT，自动附加 LIMIT 保护）。
    仅用于不涉及指标库标准指标的简单查询兜底；涉及标准指标必须走 nl2sql_query。
    :param query: SELECT 语句
    """
    monitor.report_tool(tool_name="自定义SQL查询：execute_sql_query", args={"query": query[:120]})
    stripped = query.strip().rstrip(";")
    if not stripped.lower().startswith("select"):
        return "仅允许 SELECT 查询（只读账号约束）。"
    if _READONLY_BLOCK.search(stripped):
        return "检测到非只读关键字，已拒绝执行。"
    if not re.search(r"\blimit\b", stripped, re.I):
        stripped += " limit 100"
    return _run_query(stripped)
