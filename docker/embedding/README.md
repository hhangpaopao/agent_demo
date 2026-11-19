# 向量化模型目录

`docker-compose.yml` 里的 `embedding` 服务（TEI）会把这里的
`bge-large-zh-v1.5/` 挂载到容器 `/models/bge-large-zh-v1.5`。

## 获取模型文件（二选一）

```bash
# A. 从原问数项目复制（推荐，无需下载）
cp -r /path/to/WenShu_python/docker/embedding/bge-large-zh-v1.5 .

# B. 走 HF 镜像下载（约 1.3GB）
pip install -U huggingface_hub
HF_ENDPOINT=https://hf-mirror.com hf download BAAI/bge-large-zh-v1.5 --local-dir bge-large-zh-v1.5
```

目录内应有：`config.json`、`pytorch_model.bin`、`tokenizer.json`、`vocab.txt`、`1_Pooling/` 等。

## 为什么必须是 1024 维

Qdrant 里的列/指标向量是按 1024 维建的（`embedding_size: 1024`）。
换模型或换维度就意味着**必须重建整个向量索引**，否则召回结果全错。

## 启动后自检

```bash
docker compose logs -f embedding      # 首次加载模型 30~90s，出现 "Ready" 即成功
curl http://127.0.0.1:8081/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"input":"华北地区销售总额","model":"BAAI/bge-large-zh-v1.5"}'
# 期望：{"data":[{"embedding":[1024 个浮点数...]}]}
```
