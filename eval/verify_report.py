"""报告确定性核验器 —— 只读旁路，不碰主链路、不调 LLM、不回灌报告。

用法：
    python eval/verify_report.py                 # 核验全部 session
    python eval/verify_report.py session_test01  # 只核验一个
    python eval/verify_report.py --json          # 输出 JSON（供程序消费）

三件事：数字溯源（报告业务数字能否在 evidence 找到出处）、图片可访问、口径标注齐全。
失败只出清单，不阻塞任何流程；网络不可用等异常一律降级为 UNKNOWN，不误判为 FAIL。
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
LAYERS = ("data", "network", "document")
# 只抓业务数字：带千分位金额、小数、百分比、件数；年份/日期/章节号不会被匹配
NUM_RE = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+%?|\d+%|\d+\s*件)(?![\w])")
IMG_RE = re.compile(r"https?://[^\s)]+?\.(?:jpg|jpeg|png|webp)", re.I)
CALIBER = {
    "内部成交口径": "口径隔离声明",
    "未检索到": "公开数据缺口声明",
    "模拟数据": "合成数据声明",
    "未证实存在": "虚构产品识别",
    "未采集": "采集缺口声明",
    "不清楚": "信息不足兜底",
}


def report_path(session: Path):
    files = [p for p in session.glob("*.md") if not p.name.startswith(("index", "README"))]
    return files[0] if files else None


def check_numbers(session: Path, report: str):
    """分层提取 evidence 数字，反向查报告业务数字是否有出处。"""
    sources = {}
    for layer in LAYERS:
        d = session / "evidence" / layer
        sources[layer] = set()
        for f in d.glob("*"):  # findings + raw + sources，报告推导数字常只在 raw 里
            if f.is_file():
                sources[layer] |= set(NUM_RE.findall(f.read_text(encoding="utf-8", errors="ignore")))
    # events.jsonl 含工具原始返回，是最终极的出处（evidence 落盘可能遗漏）
    ev = session / "events.jsonl"
    if ev.exists():
        sources["events"] = set(NUM_RE.findall(ev.read_text(encoding="utf-8", errors="ignore")))
    all_src = set().union(*sources.values())
    # 过滤报告自定的评分阈值/系数（0.xx）：这类数字是方法论参数，不是事实性业务数字
    report_nums = {n for n in NUM_RE.findall(report)
                   if ("%" in n or "件" in n or "," in n
                       or float(n.rstrip("%")) >= 1)}
    unknown = sorted(n for n in report_nums if n not in all_src)
    if not sources or not any(sources.values()):
        return "UNKNOWN", "evidence 缺失，无法溯源", unknown
    # 未知数字只提示待确认，不直接判幻觉（报告中的换算/四舍五入本就未必出现在 evidence）
    return ("PASS" if not unknown else "WARN",
            f"报告业务数字 {len(report_nums)} 个，evidence 可溯源 {len(report_nums) - len(unknown)} 个；"
            f"命中分布 " + "/".join(f"{k}:{len(v)}" for k, v in sources.items()),
            unknown)


def check_images(report: str):
    """逐个 GET 前 1 字节（HEAD 常被电商 CDN 拒），非 200 记为失效。"""
    urls = sorted(set(IMG_RE.findall(report)))
    if not urls:
        return "WARN", "报告无图片直链（若为主动降级需有声明）", []
    bad, checked = [], 0
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status not in (200, 206):
                    bad.append(f"{resp.status} {url}")
                checked += 1
        except Exception as exc:  # 网络不通/超时：不误判为图片失效
            bad.append(f"UNKNOWN({type(exc).__name__}) {url}")
    if not bad:
        return "PASS", f"{checked}/{len(urls)} 张图可访问", []
    if all("UNKNOWN" in b for b in bad):
        return "UNKNOWN", f"{len(urls)} 张图，网络不可用无法核验", bad
    return ("FAIL", f"{checked}/{len(urls)} 张图可访问", bad)


def check_caliber(report: str):
    hit = [label for kw, label in CALIBER.items() if kw in report]
    if not hit:
        return "FAIL", "未检出任何口径/诚实性声明", []
    return ("PASS" if len(hit) >= 2 else "WARN",
            "已声明：" + "、".join(hit), [])


def verify(session: Path, check_img: bool = True):
    rp = report_path(session)
    if not rp:
        return {"session": session.name, "status": "SKIP", "reason": "无报告文件"}
    report = rp.read_text(encoding="utf-8")
    checks = [("数字溯源", lambda: check_numbers(session, report)),
              ("口径标注", lambda: check_caliber(report))]
    if check_img:
        checks.append(("图片可访问", lambda: check_images(report)))
    results = {}
    for name, fn in checks:
        try:
            status, detail, extra = fn()
        except Exception as exc:  # 任一检查出错不影响其它项
            status, detail, extra = "UNKNOWN", f"检查器异常：{type(exc).__name__}: {exc}", []
        results[name] = {"status": status, "detail": detail, "extra": extra}
    return {"session": session.name, "report": rp.name,
            "checks": results,
            "status": "FAIL" if any(v["status"] == "FAIL" for v in results.values()) else "OK"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", help="只核验指定 session 目录名")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-images", action="store_true", help="跳过图片网络检查（批量快扫用）")
    args = ap.parse_args()
    if args.session:
        sessions = [OUTPUT / args.session]
    else:
        sessions = sorted(p for p in OUTPUT.glob("session_*") if p.is_dir())
    out = [verify(s, check_img=not args.no_images)
           for s in sessions if (s / "events.jsonl").exists()]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    for r in out:
        print(f"\n=== {r['session']}  [{r['status']}] ===")
        for name, v in r.get("checks", {}).items():
            print(f"  {v['status']:<7} {name}: {v['detail']}")
            for e in v.get("extra", [])[:5]:
                print(f"          · {e}")
    print(f"\n合计 {len(out)} 个 session，"
          f"异常 {sum(r['status'] == 'FAIL' for r in out)} 个")


if __name__ == "__main__":
    main()
