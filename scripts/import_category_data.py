"""新品类数据导入：把用户提供的 CSV 灌入数仓 biz_dw（dim_product + fact_order）。

设计原则（对应 code-change-standards）：
- 数据由用户提供，脚本不臆造任何一条；示例仅用于 --make-template。
- 三阶段：校验 → dry-run 预览 → 实际灌数；默认 dry-run，加 --commit 才写库。
- 事务性：两个表在同一事务内写入，任一行失败整体回滚。
- 可回滚：成功后自动写 output/rollback_<category>_<ts>.sql（DELETE 语句）。
- 复用维表：dim_customer / dim_region / dim_date 已存在且品类无关，不重建。

用法：
  python scripts/import_category_data.py --make-template data/new_categories   # 生成 CSV 模板
  python scripts/import_category_data.py --products data/new_categories/products.csv \
      --orders data/new_categories/orders.csv --category 耳机                  # dry-run 预览
  python scripts/import_category_data.py ... --commit                          # 实际写入
"""
import argparse
import csv
import sys
import time
from pathlib import Path

from mysql.connector import Error, connect

# 支持从任意目录以脚本方式运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.config import cfg

PRODUCT_FIELDS = ["product_id", "product_name", "category", "brand"]
ORDER_FIELDS = ["order_id", "product_id", "date_id", "region_id", "customer_id", "order_quantity", "order_amount"]

# 模板示例（仅示意格式，用户必须替换为真实数据；--commit 时若仍含示例行会拒绝）
TEMPLATE_PRODUCTS = [
    {"product_id": "E001", "product_name": "示例品牌 示例型号 头戴式耳机 500ml", "category": "耳机", "brand": "EXAMPLE 示例"},
]
TEMPLATE_ORDERS = [
    {"order_id": "ORD_TEMPLATE_001", "product_id": "E001", "date_id": "20250101", "region_id": "R001", "customer_id": "C001", "order_quantity": "1", "order_amount": "299.00"},
]


def _connect():
    d = cfg["db"]["dw"]
    return connect(host=d["host"], port=int(d["port"]), user=d["user"], password=d["password"],
                   database=d["database"], charset="utf8mb4", connection_timeout=10,
                   autocommit=True)  # 默认 False 会隐式开事务，导致 start_transaction() 报 "already in progress"


def _read_csv(path: str, fields: list[str]) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV 不存在: {p}")
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"CSV 为空: {p}")
    header = set(rows[0].keys())
    missing = [k for k in fields if k not in header]
    if missing:
        raise ValueError(f"CSV 缺字段 {missing}；需要: {fields}")
    return rows


def _validate(products: list[dict], orders: list[dict], category: str) -> None:
    """严格校验：主键唯一、品类一致、外键存在、金额/数量合法、无模板示例残留。"""
    pids = set()
    for i, row in enumerate(products, 1):
        for key in PRODUCT_FIELDS:
            if not str(row.get(key, "")).strip():
                raise ValueError(f"products.csv 第 {i} 行字段 {key} 为空")
        pid = row["product_id"].strip()
        if pid in pids:
            raise ValueError(f"product_id 重复: {pid}")
        pids.add(pid)
        if row["category"].strip() != category:
            raise ValueError(f"products.csv 第 {i} 行 category={row['category']!r} 与 --category={category!r} 不一致")
        if row["product_name"].strip().startswith("示例") or row["brand"].strip().startswith("EXAMPLE"):
            raise ValueError(f"products.csv 第 {i} 行仍是模板示例，请替换为真实数据")
    seen_orders = set()
    for i, row in enumerate(orders, 1):
        for key in ORDER_FIELDS:
            if not str(row.get(key, "")).strip():
                raise ValueError(f"orders.csv 第 {i} 行字段 {key} 为空")
        oid = row["order_id"].strip()
        if oid in seen_orders:
            raise ValueError(f"order_id 重复: {oid}")
        seen_orders.add(oid)
        if row["product_id"].strip() not in pids:
            raise ValueError(f"orders.csv 第 {i} 行 product_id={row['product_id']} 不在 products.csv 中")
        try:
            int(row["date_id"]); int(row["order_quantity"]); float(row["order_amount"])
        except ValueError:
            raise ValueError(f"orders.csv 第 {i} 行 date_id/order_quantity/order_amount 必须是数字")
        if row["order_id"].strip().startswith("ORD_TEMPLATE"):
            raise ValueError(f"orders.csv 第 {i} 行仍是模板示例，请替换为真实数据")


def _check_fk(conn, orders: list[dict]) -> None:
    """校验订单引用的客户/区域/日期维表主键真实存在（防止脏外键）。"""
    cur = conn.cursor()
    cust = {str(r[0]) for r in (cur.execute("SELECT customer_id FROM dim_customer"), cur.fetchall())[1]}
    reg = {str(r[0]) for r in (cur.execute("SELECT region_id FROM dim_region"), cur.fetchall())[1]}
    dates = {int(r[0]) for r in (cur.execute("SELECT date_id FROM dim_date"), cur.fetchall())[1]}
    for i, row in enumerate(orders, 1):
        if row["customer_id"].strip() not in cust:
            raise ValueError(f"orders.csv 第 {i} 行 customer_id={row['customer_id']} 在 dim_customer 不存在")
        if row["region_id"].strip() not in reg:
            raise ValueError(f"orders.csv 第 {i} 行 region_id={row['region_id']} 在 dim_region 不存在")
        if int(row["date_id"]) not in dates:
            raise ValueError(f"orders.csv 第 {i} 行 date_id={row['date_id']} 在 dim_date 不存在（需 2025 年内）")


def _make_template(outdir: str) -> None:
    d = Path(outdir)
    d.mkdir(parents=True, exist_ok=True)
    for name, fields, rows in (("products.csv", PRODUCT_FIELDS, TEMPLATE_PRODUCTS),
                               ("orders.csv", ORDER_FIELDS, TEMPLATE_ORDERS)):
        p = d / name
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader(); w.writerows(rows)
        print(f"已生成模板: {p}")
    (d / "必读.md").write_text(
        "# 数据填写要求（务必遵守）\n\n"
        "1. **品牌与型号必须真实存在、能在网上搜到**。示例：'索尼 WH-1000XM5'、"
        "'蓝月亮 深层洁净洗衣液'。禁止虚构品牌/型号——否则网络层定向检索拿不到真实主图与评价，"
        "知识库也无对应资料，报告会大量标注'未采集'。\n"
        "2. product_name 格式参照现有杯子数据：`品牌 型号 品类 规格`，"
        "如 `膳魔师 JNL-500 轻量保温杯 500ml`。\n"
        "3. brand 格式：`英文大写 中文`，如 `THERMOS 膳魔师`、`SONY 索尼`；白牌填 `白牌`。\n"
        "4. product_id 全局唯一，不与现有 P001~P029 冲突（建议耳机 E001+，洗衣液 L001+）。\n"
        "5. orders.csv 的 customer_id 只能填 C001~C100，region_id 填 R001~R006，"
        "date_id 填 20250101~20251231（这三张维表已存在，新品类直接复用）。\n"
        "6. order_amount 是金额（元），order_quantity 是件数；销售额应约等于单价×件数，保持合理。\n"
        "7. 模板里的示例行是占位符，必须全部替换；留着示例行 --commit 会被拒绝。\n",
        encoding="utf-8",
    )
    print(f"已生成填写说明: {d / '必读.md'}")


def _commit(conn, products: list[dict], orders: list[dict], category: str) -> Path:
    """事务性写入 + 自动生成回滚 SQL。"""
    cur = conn.cursor()
    try:
        conn.start_transaction()
        for row in products:
            cur.execute("INSERT INTO dim_product (product_id, product_name, category, brand) VALUES (%s,%s,%s,%s)",
                        (row["product_id"].strip(), row["product_name"].strip(), row["category"].strip(), row["brand"].strip()))
        for row in orders:
            cur.execute("INSERT INTO fact_order (order_id, customer_id, product_id, date_id, region_id, order_quantity, order_amount) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (row["order_id"].strip(), row["customer_id"].strip(), row["product_id"].strip(),
                         int(row["date_id"]), row["region_id"].strip(), int(row["order_quantity"]), float(row["order_amount"])))
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    pids = [r["product_id"].strip() for r in products]
    oids = [r["order_id"].strip() for r in orders]
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = cfg["paths"]["output_dir"] if "output_dir" in cfg.get("paths", {}) else "output"
    rb = Path(out) / f"rollback_{category}_{ts}.sql"
    rb.parent.mkdir(parents=True, exist_ok=True)
    in_clause = ",".join("'" + x.replace("'", "''") + "'" for x in oids)
    pin = ",".join("'" + x.replace("'", "''") + "'" for x in pids)
    rb.write_text(
        f"-- 回滚 {category} 导入（先删订单再删商品）\n"
        f"DELETE FROM fact_order WHERE order_id IN ({in_clause});\n"
        f"DELETE FROM dim_product WHERE product_id IN ({pin}) AND category='{category}';\n",
        encoding="utf-8",
    )
    return rb


def main() -> None:
    ap = argparse.ArgumentParser(description="新品类数据导入（默认 dry-run，--commit 才写库）")
    ap.add_argument("--make-template", metavar="DIR", help="生成 CSV 模板到指定目录")
    ap.add_argument("--products", help="商品 CSV 路径")
    ap.add_argument("--orders", help="订单 CSV 路径")
    ap.add_argument("--category", help="品类名，须与 products.csv 的 category 一致")
    ap.add_argument("--commit", action="store_true", help="实际写入数据库（不加则只预览）")
    args = ap.parse_args()

    if args.make_template:
        _make_template(args.make_template)
        return
    if not (args.products and args.orders and args.category):
        ap.error("需提供 --products / --orders / --category，或用 --make-template")

    products = _read_csv(args.products, PRODUCT_FIELDS)
    orders = _read_csv(args.orders, ORDER_FIELDS)
    _validate(products, orders, args.category)

    conn = _connect()
    try:
        _check_fk(conn, orders)
        print(f"[校验通过] 品类={args.category} 商品={len(products)} 条 订单={len(orders)} 条；外键维表均存在")
        if not args.commit:
            print("\n[dry-run 预览] 未写入数据库。前 5 条商品/订单：")
            for r in products[:5]: print("  商品:", r)
            for r in orders[:5]: print("  订单:", r)
            print("\n确认无误后加 --commit 重新执行")
            return
        rb = _commit(conn, products, orders, args.category)
        print(f"\n[写入成功] 回滚脚本: {rb}")
    except (ValueError, Error) as e:
        print(f"[失败/已回滚] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
