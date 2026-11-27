# -*- coding: utf-8 -*-
"""
Eval 闭环判分器（零新服务 / 纯标准库 / 只读 output 产物）。

把真实跑过的 session 变成可复现的「过程质量基线」，回答两个问题：
  1. 系统这次有没有按设计跑（三源采集、证据落盘、预算合规、无空转）；
  2. 报告有没有守诚实底线（口径标注、虚构产品不编造、主图/降级声明）。

不判「答案正确率」——那需要 ground truth，本项目没有 30 题标注集；
eval/ 只读已有 events.jsonl / 报告 / evidence，不调用 LLM、不起服务。

用法：
    python eval/run_eval.py                       # 判所有 session，打印表
    python eval/run_eval.py --baseline            # 判并把结果写为 baseline.json
    python eval/run_eval.py --compare             # 与 baseline.json 对比（回归）
    python eval/run_eval.py --self-test           # 判分器双向自测（应 6/6 过、0/6 乱答过）
    python eval/run_eval.py --session session_test01   # 只判一个
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
BASELINE = Path(__file__).resolve().parent / "baseline.json"

# 判分阈值：与源码常量对齐，改源码时这里必须同步（README 有出处表）
LIMITS = {
    "知识库检索": 16,   # agent/subagents/knowledge_base_agent.py:9
    "SQL查询": 16,      # agent/subagents/database_query_agent.py:12
    "网络搜索": 5,      # conf/config.yaml:22 SEARCH_MAX_CALLS
    "主图解析": 10,     # tools/vlm_tools.py:22
    "助手提问": 8,      # tools/ragflow_tools.py:313
}
STUCK_THRESHOLD = 4    # api/monitor.py:116
COLLECTORS = {"database_query_agent", "knowledge_base_agent", "network_search_agent"}
THREE_LAYERS = ("data", "network", "document")
EVIDENCE_FILES = ("findings.md", "raw.md", "sources.json")

# 事件里的工具名 -> 预算类别
TOOL_CATEGORY = {
    "知识库直接检索": "知识库检索",
    "自定义SQL查询": "SQL查询",
    "网络搜索": "网络搜索",
    "主图解析": "主图解析",
    "知识库提问": "助手提问",
}


def _signature(data):
    """工具参数签名：用于判同工具同参数重复（与 monitor 的 STUCK 判定同思路）"""
    args = data.get("args", {}) if isinstance(data, dict) else {}
    raw = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _load_events(path):
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _tool_counts(rows):
    counts = {}
    sig_seen = {}
    for r in rows:
        if r.get("event") != "tool_start" or not isinstance(r.get("data"), dict):
            continue
        name = r["data"].get("tool_name", "")
        category = next((c for p, c in TOOL_CATEGORY.items() if name.startswith(p)), None)
        if category:
            counts[category] = counts.get(category, 0) + 1
        key = (name, _signature(r["data"]))
        sig_seen[key] = sig_seen.get(key, 0) + 1
    return counts, sig_seen


def grade_session(session_dir):
    """判一个 session，返回 dict；文件缺失的维度标 NA 而非伪造"""
    sid = session_dir.name
    result = {"session": sid, "checks": {}}

    events_path = session_dir / "events.jsonl"
    if not events_path.exists():
        return {"session": sid, "error": "events.jsonl 缺失", "checks": {}}
    rows = _load_events(events_path)

    # 1. 三源采集覆盖
    called = {r["data"].get("assistant_name") for r in rows
              if r.get("event") == "assistant_call" and isinstance(r.get("data"), dict)}
    hit = sorted(COLLECTORS & called)
    result["checks"]["三源采集"] = {
        "status": "PASS" if len(hit) == 3 else "FAIL",
        "detail": f"{len(hit)}/3: {','.join(hit)}",
    }

    # 2. 证据落盘（三层 × 三文件 + index）
    ev_dir = session_dir / "evidence"
    missing = [f"{layer}/{fn}" for layer in THREE_LAYERS for fn in EVIDENCE_FILES
               if not (ev_dir / layer / fn).exists()]
    if not (ev_dir / "index.md").exists():
        missing.append("index.md")
    result["checks"]["证据落盘"] = {
        "status": "PASS" if not missing else "FAIL",
        "detail": "10/10 文件齐全" if not missing else f"缺 {len(missing)}: {','.join(missing[:4])}",
    }

    # 3. 预算合规（每类调用数 <= 上限）
    counts, sig_seen = _tool_counts(rows)
    over = [f"{k}={counts[k]}>{lim}" for k, lim in LIMITS.items() if counts.get(k, 0) > lim]
    result["checks"]["预算合规"] = {
        "status": "PASS" if not over else "FAIL",
        "detail": ", ".join(f"{k}={counts.get(k,0)}/{lim}" for k, lim in LIMITS.items())
                  + (" | 超限: " + ",".join(over) if over else ""),
    }

    # 4. 空转检测（同工具同参数 >=4 次 或 stuck_detected）
    repeats = [(k[0], v) for k, v in sig_seen.items() if v >= STUCK_THRESHOLD]
    stuck = [r for r in rows if r.get("event") == "stuck_detected"]
    result["checks"]["无空转"] = {
        "status": "FAIL" if (repeats or stuck) else "PASS",
        "detail": f"stuck事件={len(stuck)}, 同参数重复>={STUCK_THRESHOLD}的组合={len(repeats)}",
    }

    # 5. 报告存在 + 结构
    reports = sorted(session_dir.glob("*.md"))
    report = reports[0] if reports else None
    if not report:
        result["checks"]["报告结构"] = {"status": "NA", "detail": "无报告文件"}
        result["checks"]["诚实标注"] = {"status": "NA", "detail": "无报告文件"}
        result["checks"]["主图/降级"] = {"status": "NA", "detail": "无报告文件"}
        return result
    text = report.read_text(encoding="utf-8")
    chapters = len(re.findall(r"^## ", text, re.M))
    chars = len(text)
    result["checks"]["报告结构"] = {
        "status": "PASS" if chapters >= 5 and chars >= 3000 else "FAIL",
        "detail": f"{report.name} | {chars}字 | {chapters}章",
    }

    # 6. 诚实标注：口径隔离 + 缺失声明（至少各命中一类）
    honesty_kw = ("内部成交口径", "口径", "模拟数据", "未检索到", "未采集", "不清楚", "降级声明")
    honesty_hits = {kw: text.count(kw) for kw in honesty_kw if kw in text}
    has_caliber = any(k in honesty_hits for k in ("内部成交口径", "口径"))
    has_gap = any(k in honesty_hits for k in ("未检索到", "未采集", "不清楚", "降级声明"))
    result["checks"]["诚实标注"] = {
        "status": "PASS" if (has_caliber and has_gap) else "FAIL",
        "detail": f"口径标注={'有' if has_caliber else '无'}, 缺口声明={'有' if has_gap else '无'}, "
                  + ",".join(f"{k}x{v}" for k, v in sorted(honesty_hits.items(), key=lambda x: -x[1])[:4]),
    }

    # 7. 主图/降级：有 analyze_product_image 调用 或 报告有图链 或 明确降级声明
    img_calls = sum(1 for r in rows if r.get("event") == "tool_start"
                    and isinstance(r.get("data"), dict)
                    and r["data"].get("tool_name", "").startswith("主图解析"))
    img_links = len(set(re.findall(r"https?://[^\s)]+\.(?:jpg|jpeg|png|webp)", text, re.I)))
    downgrade = any(k in text for k in ("降级声明", "未获取到本品类真实商品图", "主图对比不成立"))
    result["checks"]["主图/降级"] = {
        "status": "PASS" if (img_calls or img_links or downgrade) else "FAIL",
        "detail": f"VLM调用={img_calls}, 图链={img_links}, 降级声明={'有' if downgrade else '无'}",
    }

    passed = sum(1 for c in result["checks"].values() if c["status"] == "PASS")
    total = sum(1 for c in result["checks"].values() if c["status"] != "NA")
    result["passed"] = passed
    result["total"] = total
    return result


def summarize(results):
    valid = [r for r in results if "checks" in r and r["checks"]]
    dims = {}
    for r in valid:
        for name, c in r["checks"].items():
            if c["status"] == "NA":
                continue
            d = dims.setdefault(name, {"pass": 0, "total": 0})
            d["total"] += 1
            d["pass"] += 1 if c["status"] == "PASS" else 0
    return {
        "sessions": len(valid),
        "by_dimension": {k: {"pass": v["pass"], "total": v["total"],
                             "rate": round(v["pass"] / v["total"] * 100, 1)} for k, v in dims.items()},
        "total_checks": sum(d["total"] for d in dims.values()),
        "total_pass": sum(d["pass"] for d in dims.values()),
    }


def self_test():
    """双向验证：正例（设计合规）应全过，反例（乱跑）应全挂——同 eval_pilot 思路"""
    tmp = Path(__file__).resolve().parent / "_selftest"
    (tmp / "evidence" / "data").mkdir(parents=True, exist_ok=True)
    rows = [
        {"event": "session_created", "data": {}},
        *[{"event": "assistant_call", "data": {"assistant_name": n}}
          for n in ("database_query_agent", "knowledge_base_agent", "network_search_agent")],
        {"event": "tool_start", "data": {"tool_name": "网络搜索：internet_search", "args": {"q": "a"}}},
    ]
    (tmp / "events.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    for fn in EVIDENCE_FILES:
        (tmp / "evidence" / "data" / fn).write_text("x", encoding="utf-8")
    for layer in ("network", "document"):
        (tmp / "evidence" / layer).mkdir(parents=True, exist_ok=True)
        for fn in EVIDENCE_FILES:
            (tmp / "evidence" / layer / fn).write_text("x", encoding="utf-8")
    (tmp / "evidence" / "index.md").write_text("x", encoding="utf-8")
    good = ("## 一、口径\n内部成交口径 x\n## 二、缺口\n未检索到 x\n## 三、结论\n未采集 x\n"
            "## 四、建议\n## 五、主图\n降级声明：未获取到本品类真实商品图\n") + "字" * 3100
    bad = "随便写点东西"

    # 正例：目录里只放一份报告，避免 sorted(glob) 选错文件
    (tmp / "正例.md").write_text(good, encoding="utf-8")
    pos = grade_session(tmp)
    (tmp / "正例.md").unlink()
    # 反例：报告结构/诚实标注必挂
    (tmp / "反例.md").write_text(bad, encoding="utf-8")
    neg = grade_session(tmp)
    (tmp / "反例.md").unlink()
    (tmp / "events.jsonl").unlink()
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    pos_pass = sum(1 for c in pos["checks"].values() if c["status"] == "PASS")
    neg_fail = sum(1 for n in ("报告结构", "诚实标注") if neg["checks"][n]["status"] == "FAIL")
    checks = [
        ("正例全过", pos_pass == 7, f"{pos_pass}/7"),
        ("反例报告结构挂", neg["checks"]["报告结构"]["status"] == "FAIL", ""),
        ("反例诚实标注挂", neg["checks"]["诚实标注"]["status"] == "FAIL", ""),
        ("正例三源过", pos["checks"]["三源采集"]["status"] == "PASS", ""),
        ("反例三源仍过（只看事件）", neg["checks"]["三源采集"]["status"] == "PASS", ""),
        ("不伪造维度", "checks" in pos and "checks" in neg, ""),
    ]
    ok = all(x[1] for x in checks)
    for name, passed, detail in checks:
        print(f"  [{'OK ' if passed else 'FAIL'}] {name} {detail}")
    print(f"判分器自测: {sum(1 for x in checks if x[1])}/{len(checks)} 通过 -> {'可信' if ok else '不可信'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="写 baseline.json")
    ap.add_argument("--compare", action="store_true", help="与 baseline.json 对比")
    ap.add_argument("--self-test", action="store_true", help="判分器双向自测")
    ap.add_argument("--session", help="只判指定 session 目录名")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    if args.session:
        dirs = [OUTPUT / args.session]
        if not dirs[0].exists():
            print(f"session 不存在: {args.session}")
            sys.exit(1)
    else:
        dirs = sorted(p for p in OUTPUT.glob("session_*") if p.is_dir())
    if not dirs:
        print("output/ 下无 session_* 目录")
        sys.exit(1)

    results = [grade_session(d) for d in dirs]
    print("=" * 78)
    print("Eval 闭环 —— 过程质量基线（只读已有产物，不判答案正确率）")
    print("=" * 78)
    for r in results:
        print(f"\n■ {r['session']}")
        if "error" in r:
            print("  " + r["error"])
            continue
        for name, c in r["checks"].items():
            mark = {"PASS": "OK ", "FAIL": "FAIL", "NA": " NA "}[c["status"]]
            print(f"  [{mark}] {name:<8} {c['detail']}")
        print(f"  -> {r.get('passed',0)}/{r.get('total',0)}")

    s = summarize(results)
    print("\n" + "=" * 78)
    print(f"汇总: {s['sessions']} 个 session, {s['total_pass']}/{s['total_checks']} 项通过")
    for name, d in s["by_dimension"].items():
        print(f"  {name:<8} {d['pass']}/{d['total']} = {d['rate']}%")
    print("=" * 78)

    if args.baseline:
        payload = {"note": "过程质量基线，非答案正确率；阈值出处见 README",
                   "dimensions": s, "sessions": results}
        BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n基线已写入 {BASELINE}")
    if args.compare:
        if not BASELINE.exists():
            print("\n无 baseline.json，请先跑 --baseline")
            sys.exit(1)
        old = json.loads(BASELINE.read_text(encoding="utf-8"))["dimensions"]
        print("\n=== 与基线对比（回归）===")
        for name, d in s["by_dimension"].items():
            prev = old["by_dimension"].get(name, {}).get("rate")
            if prev is None:
                delta = "新增"
            else:
                diff = round(d["rate"] - prev, 1)
                delta = f"{'+' if diff > 0 else ''}{diff}pp"
            print(f"  {name:<8} {prev if prev is not None else 'NA'}% -> {d['rate']}%  ({delta})")


if __name__ == "__main__":
    main()
