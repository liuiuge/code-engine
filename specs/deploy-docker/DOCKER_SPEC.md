# docker 一键部署（P1-10 子任务）

> 归属：Role 1 PM（`specs/<feature-slug>/<NAME>.md`）
> 关联：阶段一 `specs/PHASE1_PLAN.md` 的 P1-10；依赖 **P1-8**（`/health` readiness 校验）、其余功能。
> 性质：**开发就绪的子任务 spec**（含 `## Acceptance Criteria` + `## Test Scenarios`）。
> Wave：W3（纯部署，独立交付）。

---

## 1. 范围与边界

本 spec 只覆盖「**docker compose up 即用**」：

### 要做什么
1. 新增 `deploy/Dockerfile`：基于 `python:3.13-slim`；安装系统 `go`（`apt-get install -y golang-go` 或下载官方二进制）、`pip install -r requirements.txt`、拷贝代码、`EXPOSE 8000`、入口 `deploy/entrypoint.sh`。
2. 新增 `deploy/docker-compose.yml`：services `app`（build 本 Dockerfile，映射 `8000:8000`，`depends_on` ollama 健康）+ `ollama`（`ollama/ollama` 镜像，暴露 `11434`）。`app` 环境变量 `OLLAMA_BASE_URL=http://ollama:11434`（与 `infrastructure/models.yaml` base_url 对齐）。
3. 新增 `deploy/entrypoint.sh`：等 ollama 就绪（`curl http://ollama:11434/api/tags` 或 `ollama list`）+ `go version` 校验 → 读 `models.yaml` `default` 预拉模型（`ollama pull <default>`）→ `uvicorn web.main:app --host 0.0.0.0 --port 8000`（单 worker，见 P1-6 注意）。
4. 部署文档（`README.md` 或 `deploy/README.md`）：仅含 `docker compose up` 步骤。

### 不做什么（本任务边界）
- 不接 k8s / helm（MVP 单机 compose）。
- 不预置 GPU（CPU 推理即可跑通冒烟）。
- 不改应用代码逻辑（仅新增部署产物）。

---

## 2. 处理流程（mermaid）

```mermaid
flowchart TD
    UP[docker compose up] --> APP[app 容器]
    UP --> OLL[ollama 容器]
    APP --> ENT[entrypoint.sh]
    ENT --> WAIT[等 ollama ready + go version]
    WAIT --> PULL[ollama pull default 模型]
    PULL --> UV[uvicorn web.main:app]
    OLL --> TAGS[/api/tags]
    TAGS -->|就绪| UV
```

---

## 3. 组件与契约（供 Dev 实现参考，非代码）

### 3.1 Dockerfile（建议 `deploy/Dockerfile`）
```dockerfile
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends golang-go curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
```
（`.dockerignore` 排除 `output/`、`__pycache__`、`*.pyc`、`.git`、`.workbuddy` 以减小上下文。）

### 3.2 docker-compose.yml（建议 `deploy/docker-compose.yml`）
```yaml
services:
  ollama:
    image: ollama/ollama
    ports: ["11434:11434"]
    volumes: ["ollama_data:/root/.ollama"]
  app:
    build: .
    ports: ["8000:8000"]
    environment:
      - OLLAMA_BASE_URL=http://ollama:11434
    depends_on:
      ollama:
        condition: service_healthy
    volumes:
      - app_output:/app/output
volumes:
  ollama_data:
  app_output:
```
（`ollama` 加 `healthcheck: curl -fsS http://localhost:11434/api/tags`。）

### 3.3 entrypoint.sh（建议 `deploy/entrypoint.sh`）
```sh
#!/usr/bin/env bash
set -euo pipefail
echo "waiting for ollama..."
until curl -fsS http://ollama:11434/api/tags >/dev/null 2>&1; do sleep 2; done
go version
DEFAULT_MODEL=$(python -c "import yaml,sys; print(yaml.safe_load(open('infrastructure/models.yaml'))['default'])")
echo "pre-pulling model: $DEFAULT_MODEL"
ollama pull "$DEFAULT_MODEL" || echo "warn: pre-pull failed, will pull on first use"
exec uvicorn web.main:app --host 0.0.0.0 --port 8000
```

---

## 4. Acceptance Criteria

### D-01 — 干净机器 compose up 后服务可用且能生成
- **Given** 干净机器（仅装 docker + compose），无 ollama/go/pip 预装。
- **When** `docker compose up -d`（或 `up`）。
- **Then** 启动完成后：`/ui` 可访问（HTTP 200）、`/health` 健康（`status=ok`，go+ollama ready）、能跑通一次 generate（two-sum 类，返回终态、不 500）。

### D-02 — 部署文档仅含 compose 步骤
- **Given** 部署文档。
- **When** 审阅步骤。
- **Then** 仅需 `docker compose up`（及可选 `ollama pull` 已在 entrypoint 自动完成）；无需手动装 ollama / go / pip。

---

## 5. Test Scenarios（部署验收，手动/CI）

> 验收位置：CI 或本地 `docker compose up` 冒烟（非 pytest 单测，因依赖容器运行时）。可加一个轻量 CI job 跑下列步骤。

| 场景 | 覆盖 AC | 说明 |
|------|---------|------|
| 干净环境 `docker compose up -d` → `curl /health` == ok → `curl /ui` 200 → `POST generate`（two-sum）→ 终态可达 | D-01 | 容器冒烟，断言三处可达 |
| 文档审核：步骤仅 compose | D-02 | 人工/CI 检查 README 无手动装依赖步骤 |

---

## 6. 依赖与注意

- 依赖：P1-8（`/health` readiness 校验，作为启动健康判据）。
- 注意：**单 worker**（P1-6 进程内 job store）；compose 内 `uvicorn` 不写 `--workers`，默认 1。
- 注意：ollama 模型预拉可能耗时，entrypoint 超时保护；首次启动慢属正常，不视为失败。
- 注意：挂载 `app_output` 卷以免容器重建丢 `output/` 数据（可选但推荐）。
- 注意：`.dockerignore` 必须排除 `.workbuddy` 与 `.git`，避免把沙箱/密钥拷进镜像。

---

## 7. 人类校验指引（Manual Acceptance）

除部署冒烟外，须可手动验收。
**环境**：干净机器，已装 docker + docker compose。

| AC | 人类校验步骤 | 通过判定 | 失败判定 |
|----|------------|---------|---------|
| D-01 | `docker compose up -d` → 等启动 → 浏览器开 `/ui`；`curl /health`；点生成 two-sum | `/ui` 可访问、`/health` ok、生成跑通出终态 | 起不来 / `/health` degraded / 生成 500 |
| D-02 | 看部署文档（README/deploy/README） | 仅 compose 步骤即可用 | 需手动装 ollama/go/pip |
