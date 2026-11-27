# Eval 闭环 —— 过程质量基线与回归

零新服务、纯标准库、只读 `output/session_*/` 已有产物，不改主链路、不调 LLM。
定位：**过程质量评测**（系统有没有按设计跑、报告有没有守诚实底线），不是答案正确率。

## 一、为什么这样做（诚实边界）

- 项目没有 30 题标注黄金集。`desktop/Agent/eval_pilot/` 那套是 deep_pro 的杯子数据，
  其 PILOT_REPORT 已写明"系统只真答过 1 题，1/1=100% 没有统计意义"。
- 所以本评测评的是**可从真实产物客观判定的过程维度**，绝不把黄金答案冒充系统输出刷分。
- 简历可写："建过程质量评测基线，4 会话 26/28 项通过，判分器双向自测 6/6"——
  这是真数字；**不要写"正确率 93%"**，那需要标注集，本项目没有。

## 二、判什么（7 个维度，源码阈值出处）

| 维度 | 通过条件 | 阈值出处 |
|---|---|---|
| 三源采集 | 三个采集 agent 都出现在 assistant_call | `agent/subagents/*.py` |
| 证据落盘 | `evidence/{data,network,document}/{findings,raw,sources}` + index.md 齐全 | `tools/evidence_tools.py` |
| 预算合规 | 各类调用数 ≤ 上限 | 知识库16 (`knowledge_base_agent.py:9`)、SQL16 (`database_query_agent.py:12`)、联网5 (`conf/config.yaml:22`)、主图10 (`vlm_tools.py:22`)、助手提问8 (`ragflow_tools.py:313`) |
| 无空转 | 无 stuck_detected 事件、无同工具同参数 ≥4 次 | `api/monitor.py:116` |
| 报告结构 | ≥5 个 `##` 章节 且 ≥3000 字 | 本项目报告实测共识 |
| 诚实标注 | 同时有口径标注与缺口声明（未检索到/未采集/不清楚/降级） | `prompts/analysis_agent.md` |
| 主图/降级 | 有 VLM 调用 或 图片直链 或 降级声明 | `prompts/analysis_agent.md` 主图优先级 |

> 改任何上述源码常量，必须同步 `run_eval.py` 顶部 `LIMITS`，否则基线失真。

## 三、怎么跑

```bash
cd C:/Users/35448/Desktop/Agent/deep_pro
python eval/run_eval.py --self-test      # 判分器双向自测：正例满分 + 反例必挂，应 6/6
python eval/run_eval.py                  # 判所有 session 并打印
python eval/run_eval.py --baseline       # 重新固化基线（baseline.json）
python eval/run_eval.py --compare        # 与新会话对比，看每个维度涨跌 pp
python eval/run_eval.py --session session_test01   # 只判一个
```

典型回归流程：改 prompt 或限流代码 → 跑一次新 session（新目录 `session_*`）→ `python eval/run_eval.py --compare` →
看哪个维度掉了。掉分项直接指向回退点，不需要翻几万行日志。

## 四、当前基线（2026-11-27，4 个真实 session）

- 判分器自测：**6/6**（正例 7/7 通过，反例报告结构/诚实标注正确判挂）
- 总体：**26/28** 项通过
- 分维度：三源采集 4/4、证据落盘 4/4、预算合规 4/4、报告结构 4/4、诚实标注 4/4；
  无空转 3/4、主图/降级 3/4

两个 FAIL 均为历史真实事实（非误判）：
1. `session_productB_final_02`：早期版本 4 次 stuck_detected 告警，报告无主图/降级声明。
2. `session_test_img_002`：虚构产品 NullTech QX-9000，主图维度靠 3 条图链通过（VLM 0 次）；
   但它正确识别"未证实存在"，属诚实性通过——见报告本身。

## 五、不做什么

- 不判答案值对不对（没有 ground truth，判了就是编造）。
- 不读线上库、不联网、不调 LLM、不起服务。
- 某个 session 缺文件 → 对应维度记 NA，不伪造 PASS。
- 不把 `eval_pilot` 的黄金答案填进系统输出冒充跑分。

---

## 六、报告确定性核验器 `verify_report.py`

与 `run_eval.py`（判过程）互补：这个**判产物**，把"诚实性机制"从口号变成代码可验的清单。

```bash
python eval/verify_report.py                  # 全部 session（含图片 HEAD 检查）
python eval/verify_report.py session_test01   # 单个 session
python eval/verify_report.py --no-images      # 批量快扫（跳过图片网络请求）
python eval/verify_report.py --json           # 机器可读输出
```

**核验三项**
1. **数字溯源**：从报告抽业务数字（金额/百分比/件数，自动排除 0.xx 评分阈值），
   反查是否出现在该 session 的 evidence（findings+raw+sources）或 `events.jsonl` 的工具原始返回中。
   查不到记 WARN「疑似无出处」——可能是报告推导值或证据落盘遗漏，**不直接判幻觉**。
2. **图片可访问**：抽取报告内嵌 `http(s)://...jpg/png`，逐个发带 `Range` 头的 GET（HEAD 常被电商 CDN 拒），
   非 200/206 记 FAIL；网络异常记 UNKNOWN，不误判为图片失效。
3. **口径标注**：检查是否含"内部成交口径/未检索到/模拟数据/未证实存在/未采集/不清楚"等声明，≥2 项 PASS。

**设计纪律**：纯标准库、只读 `output/`、不调 LLM、不回灌报告、失败只出清单不阻塞——
任一检查抛异常记 UNKNOWN，不影响其它项。实测 4 个 session 全部 OK、图片 2/2 可访问。
