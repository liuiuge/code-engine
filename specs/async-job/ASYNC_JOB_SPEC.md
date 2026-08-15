# 异步 Job 化（P1-6 子任务）

> 归属：Role 1 PM（`specs/<feature-slug>/<NAME>.md`）
> 关联：阶段一 `specs/PHASE1_PLAN.md` 的 P1-6；依赖 W0 的 **P1-1 事件总线**（EB-01~04）、W1 的 **P1-3 SSE**（SE-01~03）、**P1-4 取消**（CA-01~03）。
> 性质：**开发就绪的子任务 spec**（含 `## Acceptance Criteria` + `## Test Scenarios`，每条 AC 1:1 映射回归用例）。
> Wave：W2（纯后端；前端复用 P1-5 面板用 `job_id` 恢复，见 §1.1 矩阵）。

---

## 1. 范围与边界

本 spec 只覆盖「**生成长请求转为后台 Job，可轮询/订阅恢复**」：

### 要做什么
1. 新增 `web/jobstore.py`：进程内 job store（`dict[job_id] -> JobState`）。`JobState` 含 `status`、`events`、`result`、`created_at`、`updated_at`、`cancel_event`、`notifier`。状态机 `pending → running → success | failed | cancelled`。
2. 新增 `web/routes/jobs.py`：
   - `POST /api/problems/{identifier}/generate`：由同步改为**立即返回 `{job_id, status:"running"}`（<1s）**，后台 `asyncio.create_task` 跑管线并把事件/结果写入 job store。
   - `GET /api/jobs/{job_id}`：返回 `{status, events, result}`。
   - `GET /api/jobs/{job_id}/stream`：SSE，先回放 store 中已有 `events`，再实时追加直至终态（复用 P1-3 帧 schema：`stage`/`token`/`done`/`error`）。
   - `POST /api/jobs/{job_id}/cancel`：置 `cancel_event`（衔接 P1-4）。
3. **P1-3 的 `POST .../generate/stream` 改为「创建 job + 订阅该 job 事件流」**，以 job store 为唯一事实源（避免两套流并存）；其帧 schema 不变。
4. `web/schemas.py` 增加 `JobStatus` / `JobCreated` / `JobView` 数据类。

### 不做什么（本任务边界）
- 取消逻辑本身（属 P1-4）：本任务只暴露 cancel 端点并置 `cancel_event`，不实现 cancellation token。
- 多 worker 共享存储（Redis/文件）：MVP 进程内，多 worker 部署见 §6 注意。
- 前端 UI（属 P1-5）：本任务保证 job 可查/可订阅，UI 用 `job_id` 恢复进度。

---

## 2. 处理流程（mermaid）

```mermaid
flowchart TD
    CLI[POST generate] --> RET[立即返回 job_id status=running]
    RET --> TASK[asyncio.create_task run_job]
    TASK --> SUB[bus.subscribe handler]
    TASK --> THREAD[asyncio.to_thread _do_generate]
    THREAD -->|NODE_START/END/TOKEN| BUS[EventBus]
    BUS -->|append| STORE[(JobState.events)]
    THREAD -->|终态| DONE[JobState.result + status=success/failed]
    CLI2[GET /jobs/{id} 或 /stream] --> READ[读 JobState]
    READ --> CLI2
    CANCEL[POST /jobs/{id}/cancel] --> EV[cancel_event set]
```

---

## 3. 组件与契约（供 Dev 实现参考，非代码）

### 3.1 JobState（建议放 `web/jobstore.py`）
- `job_id: str`（uuid4 hex）。
- `status: Literal["pending","running","success","failed","cancelled"]`。
- `events: list[dict]`：每条为 P1-3 帧（含 `type`/`node`/`phase`/`token`/`result`/`category`/`message`）。
- `result: dict | None`：终态等价于 `GenerateResult` 字段。
- `created_at` / `updated_at`：ISO 时间戳。
- `cancel_event: asyncio.Event`：供 P1-4 取消信号接入。
- `notifier: asyncio.Event`：SSE 等待新事件时唤醒。

### 3.2 端点 `web/routes/jobs.py`
- `create_job(identifier)`：`job_id = uuid4().hex`；`JOBS[job_id] = JobState(status="running")`；`asyncio.create_task(run_job(job_id, identifier))`；返回 `JobCreated(job_id=job_id, status="running")`。
- `run_job`：主线程 `bus.subscribe(handler)`（`handler` 把事件 append 到 `JOBS[job_id].events` 并 `notifier.set()`）；`await asyncio.to_thread(_do_generate, identifier)`；终态写入 `JOBS[job_id].result` 与 `status`；异常写入 `status="failed"` + `error`（衔接 P1-7）。
- `GET /api/jobs/{job_id}`：返回 `JobView(status, events, result)`；未知 id → 404。
- `GET /api/jobs/{job_id}/stream`：`StreamingResponse`；先 yield 已有 `events` 帧，再循环 `await notifier.wait()` 取新帧，至 `status` 终态 yield `done`/`error` 后关闭。
- `POST /api/jobs/{job_id}/cancel`：若未终态则 `cancel_event.set()` 且 `status="cancelled"`；返回确认。

### 3.3 复用点
- `_do_generate` 仍用 `web/routes/go_code.py:271`（业务不变），只在外层包 job 写盘。
- 事件来自 P1-1 的 `infrastructure.events.bus`（P1-1 待建模块，本任务消费其订阅接口）。

---

## 4. Acceptance Criteria

### J-01 — POST 立即返回 job_id 且任务仍在后台运行
- **Given** 一次 coding 题生成（two-sum 类），客户端 `POST /api/problems/{id}/generate`。
- **When** 服务端收到请求（任务实际仍在后台跑）。
- **Then** 响应在 <1s 内返回 `job_id` 且 `status` 为 `pending` 或 `running`；此时 `GET /api/jobs/{job_id}` 显示任务尚未 `success`。

### J-02 — 完成后 GET 返回 success + 等价 GenerateResult，运行中返回进度
- **Given** 同上 job。
- **When** 管线结束（成功）。
- **Then** `GET /api/jobs/{job_id}` 返回 `status="success"` 且 `result` 含 `task_name`/`success`/`build_result`/`content` 等（与 `GenerateResult` 字段等价）；运行中轮询则返回已有 `events` 进度（`status="running"`）。

### J-03 — 刷新后用 job_id 可恢复，进度/结果不丢、events 幂等
- **Given** 生成进行中或已结束的 job_id。
- **When** 客户端用同一 `job_id` 重新 `GET` 或订阅 `/stream`。
- **Then** 能恢复查看完整 `events` 序列与终态 `result`；重复订阅不会重复追加事件（`events` 幂等，不随订阅次数翻倍）。

---

## 5. Test Scenarios（映射回归用例）

> 回归用例位置：`web/tests/test_job_regression.py`（用 FastAPI `TestClient`；管线用 stub 注入 `features.solver.nodes.invoke_model`，复用 `test_verifier_regression.py` 的 stub LLM 手法；`bus.subscribe` 用真实或内存总线）。

| 场景 | 覆盖 AC | 说明 |
|------|---------|------|
| TestClient `POST generate`，断言响应含 `job_id` 且 `status` 非终态（<1s 内返回） | J-01 | 轮询 `GET /jobs/{id}` 直到终态，断言首查时 `status in (pending,running)` |
| 跑完一次，断言 `GET /jobs/{id}` 末态 `status=success` 且 `result.task_name`/`result.success` 存在 | J-02 | 解析 `JobView.result` 断言字段齐 |
| 同一 job_id 连续两次 `GET`，断言 `events` 列表长度一致、内容相同（幂等） | J-03 | 两次响应 `events` 深度相等 |

---

## 6. 依赖与注意

- 依赖：P1-1（事件总线 emit NODE_START/END/TOKEN）、P1-3（帧 schema 复用）、P1-4（`cancel_event` 接入）。
- 注意：**进程内 store 在单 worker 下正确**；多 worker（`uvicorn --workers N` 或多副本）需外部共享存储（文件/Redis），MVP 在部署文档标注「单 worker」。后续可换存储而不改端点契约。
- 注意：`run_job` 异常必须被捕获并写入 `JobState.status="failed"` + `error`（衔接 P1-7 友好失败），不得让后台任务裸抛导致 job 永远 `running`。
- 注意：保留 CLI 旧阻塞路径（`generate_for_problem`）不变；Job 化只影响 API 层。
- 注意：P1-3 的 `POST .../generate/stream` 在本任务中改为「建 job + 订阅 job 流」，避免两套流逻辑分裂。

---

## 7. 人类校验指引（Manual Acceptance）

除 `web/tests/test_job_regression.py` 外，每条 AC 须可由人类手动验收。
**环境**：`uvicorn web.main:app --port 8000 --workers 1` 启动；浏览器开 `http://127.0.0.1:8000/ui`；进入任意已缓存题目详情（如 two-sum）。

| AC | 人类校验步骤 | 通过判定 | 失败判定 |
|----|------------|---------|---------|
| J-01 | 点「生成 Go 代码」→ 观察：请求是否立即返回（面板进入进行中、不阻塞）、后端 job 是否 running | 秒回 job_id/进行中态，面板可立即操作，非等几十秒 | 点击后卡等数十秒才返回结果 |
| J-02 | 等生成结束 → `GET /api/jobs/{id}`（或面板）→ 看终态 | `status=success` 且返回含 task_name/编译结果/代码 | 一直 `running` 不终态、或结果缺字段 |
| J-03 | 生成进行中刷新浏览器标签 → 用同一 job_id 恢复（面板或 `GET`）→ 看进度/结果 | 刷新后可续看、终态与首次一致、`events` 不重复 | 刷新丢进度、或从头重跑、或事件重复翻倍 |
