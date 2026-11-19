# deep_pro

深度研究系统：**主智能体统筹 + 三专家子智能体协作**（整合 deep_search_pro / WenShu_python / zhanggui_python）。
架构与决策依据见《新项目计划书_补充完善版.md》。

## 架构

```
Main Agent（项目经理：拆解/汇总/generate_markdown/convert_md_to_pdf）
 ├─ network_search_agent  internet_search(Tavily)  ≤5次·≥3维度（工具层硬拦截）
 ├─ database_query_agent  nl2sql_query（问数图封装）+ list_tables/preview + 只读SQL兜底 + fallback_preview 保底（三层降级·3次上限代码强制）
 └─ knowledge_base_agent  get_assistant_list / create_ask_delete（内部库 + 会话资料库，双库隔离）

入库管线（掌柜裁剪版）：pdf → MinerU转md → 图片视觉转写+MinIO → RAGFlow 会话 dataset
```

## 快速开始

```bash
# 1. 基础设施（MySQL×2 / Qdrant / ES / MinIO / PostgreSQL / Redis / Embedding(TEI)；RAGFlow 用官方 compose 另行部署）
cd docker && docker compose up -d

# 2. 配置
cp .env.example .env   # 填写 LLM/Tavily/RAGFlow/数据库等

# 3. 合并三个既有项目代码（从桌面目录拉取）
python scripts/vendor_projects.py

# 4. 安装依赖并启动
uv sync
uvicorn api.server:app --reload
```

## 云服务器部署

### 需要的镜像（共 9 个）

| 来源 | 镜像 | 用途 |
|------|------|------|
| 本仓库 compose | `mysql:8.0` ×2 | 业务数仓 / 问数元数据 |
| | `qdrant/qdrant` | 问数列/指标向量召回 |
| | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.8` | **向量化服务（TEI）**，见下 |
| | `elasticsearch:8.14.0` | 问数关键词召回 |
| | `minio/minio` | 上传资料图片存储 |
| | `postgres:16` | 会话状态（checkpoint） |
| | `redis:7-alpine` | 检索结果缓存 |
| RAGFlow 官方 compose | `infiniflow/ragflow` + 自带 es/mysql/minio/redis | 企业知识库 + 用户资料库 |

### Embedding 服务（容易被漏掉）

问数图**必须**有向量化服务：`recall_column` / `recall_metric` 节点会把关键词 embed 后查 Qdrant，
Qdrant 的 `embedding_size` 必须等于模型输出维度（本项目 1024，模型 `BAAI/bge-large-zh-v1.5`）。

模型文件两种获取方式（放到 `docker/embedding/bge-large-zh-v1.5/`）：

```bash
# A. 直接从原问数项目复制（推荐，省下载）
cp -r /path/to/WenShu_python/docker/embedding/bge-large-zh-v1.5 docker/embedding/

# B. 走 HF 镜像下载
pip install -U huggingface_hub
HF_ENDPOINT=https://hf-mirror.com hf download BAAI/bge-large-zh-v1.5 \
  --local-dir docker/embedding/bge-large-zh-v1.5
```

验证：`curl http://127.0.0.1:8081/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"input":"销售额","model":"BAAI/bge-large-zh-v1.5"}'` 应返回 1024 维向量。

> RAGFlow 也可以复用这个 TEI：模型提供商 → OpenAI-API-Compatible，BaseURL 填 `http://<宿主机IP>:8081/v1`。
> 若 RAGFlow 在独立 compose 网络里，用 `host.docker.internal`（Linux 需加 `extra_hosts: host-gateway`）。

### 部署要点

1. **端口**：compose 里所有基础设施端口都已绑 `127.0.0.1`，只给容器内网和本机调试用。云上安全组只放行 **22 / 80 / 443**（应用 8000 走 Nginx 反代，不直开）。
2. **容器内互相访问用服务名**：docker 部署时 `.env` 改成
   ```ini
   QDRANT_HOST=qdrant
   ES_HOST=elasticsearch
   EMBEDDING_HOST=embedding
   META_MYSQL_HOST=mysql-meta
   DW_MYSQL_HOST=mysql-dw
   MINIO_ENDPOINT=minio:9000
   CHECKPOINT_PG_DSN=postgresql://deep_pro:deep_pro@postgres-checkpoint:5432/deep_pro
   REDIS_URL=redis://redis:6379/0
   RAGFLOW_BASE_URL=http://<宿主机内网IP>:9380
   ```
3. **ES 必须调内核参数**，否则启动即退出：`echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf && sudo sysctl -p`
4. **内存**：RAGFlow 官方要求 ≥4 核 16G；叠加本仓库 8 个服务后建议 **8 核 32G / 100G 磁盘**。
5. **WebSocket**：Nginx 反代 `/ws/` 必须加 `Upgrade`/`Connection` 头，否则握手失败。

## API

| 协议 | 路径 | 说明 |
|------|------|------|
| **WS** | `/ws/{session_id}` | 会话主通道：客户端发 `{"type":"chat","query":...}` / `{"type":"interrupt"}` / `{"type":"resume"}`；服务端推事件（统一信封 `{type:"monitor_event", event, message, session_id, data, timestamp}`）：`session_created` / `assistant_call` / `tool_start` / `tool_end` / `task_result` / `pipeline_progress` / `error` / `done` / `interrupted` |
| POST | `/api/upload_material?session_id=xxx` | 上传 pdf/md，后台入库，进度走 WS `pipeline_progress` 事件 |
| GET  | `/api/upload_status/{task_id}` | 查询入库任务状态 |
| POST | `/api/report/pdf` | 按需导出报告 PDF（失败降级返回 md） |

> 通信采用 **WebSocket**（支持中断与多轮追加）。断线重连：前端用同一 session_id 重连即可。
> 会话状态持久化在 **PostgreSQL**（`AsyncPostgresSaver`，决策7）：服务重启不丢历史；
> 中断后发 `{"type":"resume"}` 可从最后一次 checkpoint 续跑。PG 不可用时自动降级内存态。
> 检索结果经 **Redis** 缓存 10 分钟（Tavily / RAGFlow），Redis 不可用自动降级为不缓存。

## 关键决策（已定稿）

1. **nl2sql 降级**：db 子智能体内三层降级（schema改写重试→简单手写SQL→`fallback_preview` 保底+澄清）；3 次预算由 `nl2sql_query` 内部计数强制，第 4 次直接 `attempts_exhausted`；保底 SQL 由模板渲染 + 表名白名单，不让模型自己拼
2. **用户库**：会话级 `user_upload_{session_id}` + TTL 24h 惰性清理；「保存到我的资料库」迁入账号级（预留）
3. **检索全走 RAGFlow**：Milvus 全链路已裁剪
4. **图表精简路线**：MD 表格 + MinIO 图片链接
5. **输出物**：聊天=流式MD；报告=会话目录 .md；PDF=按需导出

## 目录说明

```
prompts/      提示词正文（*.md，由 core/load_prompt.py 加载，${var} 渲染）
agent/        主智能体 + 子智能体（名字/描述/工具绑定，正文从 prompts/ 读）
tools/        tavily(计数拦截) / db(只读) / nl2sql(问数适配) / db_fallback(保底预览) / ragflow(双库) / md / pdf
api/          FastAPI + monitor(WebSocket 事件推送)
import_pipeline/  裁剪版入库管线
core/         配置(omegaconf) / 上下文(ContextVar) / 提示词加载 / Redis 缓存
vendor/       scripts/vendor_projects.py 拉取的问数/掌柜代码
utils/        word_converter.py（Word COM 转 PDF）
```

### 改提示词不用碰代码

```python
from core.load_prompt import load_prompt, list_prompts

list_prompts()                                  # ['database_query_agent', 'main_agent', ...]
load_prompt("db", max_attempts=3)               # 短名 db → prompts/database_query_agent.md
load_prompt("main")                             # 无占位符，直接返回原文
```

`${var}` 走 `string.Template`（不是 `str.format`），所以提示词里的 SQL 模板 `{table_name}`、JSON 示例花括号都原样保留；缺变量会直接报错并列出缺少的键，不会把 `${max_attempts}` 原样喂给模型。

## TODO（P4）

- [x] ~~`InMemorySaver` → 持久化 checkpointer~~（已落地：PostgreSQL + 中断续跑 resume）
- [x] ~~检索结果 Redis 缓存~~（已落地：Tavily / RAGFlow 各 10 分钟）
- [ ] 会话文件 7 天过期清理
- [ ] 「保存到我的资料库」账号级 dataset 迁移接口（未登录隐藏入口）+ JWT 鉴权
- [ ] 上传配额（≤200MB/用户）与文件大小/类型校验补全
- [ ] Prometheus 指标（子代理调用数、工具耗时、token 消耗）
- [ ] 外部工具接入统一走 MCP 协议（规划项，暂不实施）
