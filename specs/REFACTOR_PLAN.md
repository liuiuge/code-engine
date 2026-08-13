# CodeEngine 重构计划

## 1. 现状与问题

当前仓库是扁平结构，所有文件都堆在根目录：

```
code-engine/
├── main.py          # CLI 入口 + 被 api.py 复用的 generate_for_problem
├── api.py           # FastAPI 服务 + 自身 main 块
├── problems.py      # LeetCode 拉题 + 自身 main 块
├── workflow.py      # LangGraph 编排
├── nodes.py         # LangGraph 节点
├── state.py         # AgentState
├── config.py        # 模型配置、prompt 加载
├── constants.py     # 常量
├── logger.py        # 日志与 trace
├── models.yaml      # 模型注册表
└── prompts/         # prompt 文件
```

问题：
1. **多个 `main` 函数/入口**：`main.py`、`api.py`、`problems.py` 都有 `if __name__ == "__main__"`，主线不清晰。
2. **网络服务与能力混在同一层**：FastAPI 服务应该是一级入口，而拉题、解题只是被调用的功能。
3. **基础设施与业务未隔离**：`config.py`/`constants.py`/`logger.py` 和 `workflow.py`/`nodes.py`/`problems.py` 平铺在一起。
4. **导入关系隐式依赖根目录**：`config.py` 通过 `Path(__file__).resolve().parent` 找 `prompts/` 和 `models.yaml`，移动模块后容易失效。

## 2. 重构目标

1. **网络服务作为主线**：`uvicorn web.main:app` 是唯一生产入口。
2. **拉题与 LangGraph 解题作为功能包**：被 web 层调用，也可独立测试。
3. **功能包内保留 `example/` 目录**：仅存放该功能的测试/调试 `main` 入口。
4. **基础设施独立成包**：`config`/`constants`/`logger` 等放到 `infrastructure/`，与功能包解耦。

## 3. 目标目录结构

```
code-engine/
├── web/                              # 网络服务主线
│   ├── __init__.py
│   ├── main.py                       # uvicorn 入口：创建 app 并启动
│   ├── api.py                        # FastAPI app 工厂 + 路由挂载
│   ├── schemas.py                    # Pydantic Request/Response 模型
│   ├── dependencies.py               # 共享依赖（路径、服务实例）
│   └── routes/
│       ├── __init__.py
│       ├── meta.py                   # /health, /api/stats
│       ├── problems.py               # /api/problems/* 路由
│       └── go_code.py                # /api/go-code/* 路由
│
├── features/                         # 业务功能包
│   ├── __init__.py
│   ├── problems/                     # 功能 1：拉取 LeetCode 题目
│   │   ├── __init__.py
│   │   ├── client.py                 # GraphQL HTTP 客户端
│   │   ├── models.py                 # normalize_problem, render_problem_markdown
│   │   ├── storage.py                # save/load/index 操作
│   │   ├── service.py                # resolve_problem, enrich_problem_set
│   │   └── example/
│   │       └── main.py               # 测试入口：python -m features.problems.example.main
│   │
│   └── solver/                       # 功能 2：LangGraph 解题
│       ├── __init__.py
│       ├── state.py                  # AgentState
│       ├── constants.py              # solver 内部常量（或移入 infrastructure）
│       ├── nodes.py                  # 各节点逻辑
│       ├── workflow.py               # StateGraph 编排
│       ├── executor.py               # go fmt / go build 执行器
│       ├── service.py                # run_pipeline, generate_for_problem
│       └── example/
│           └── main.py               # 测试入口：python -m features.solver.example.main
│
├── infrastructure/                   # 基础设施
│   ├── __init__.py
│   ├── paths.py                      # PROJECT_ROOT, PROMPT_DIR, OUTPUT_DIR 等统一路径
│   ├── logger.py                     # 日志与 trace 装饰器（保持无依赖）
│   ├── constants.py                  # 全局常量
│   ├── config.py                     # models.yaml + prompt 加载 + invoke_model
│   └── models.yaml                   # 模型注册表
│
├── prompts/                          # prompt 文件（位置不变，由 infrastructure/paths.py 指向）
├── frontend/                         # 静态 UI
├── output/                           # 生成物
├── scripts/
│   └── run_server.py                 # 便捷启动脚本（可选）
├── requirements.txt
├── README.md
└── REFACTOR_PLAN.md
```

## 4. 关键改动说明

### 4.1 web 层（主线）

- `web/main.py`
  - 从 `web.api import app`。
  - 提供 `if __name__ == "__main__"` 块，内部 `uvicorn.run(app, ...)`。
  - 生产启动命令：`uvicorn web.main:app --reload --port 8000`。

- `web/api.py`
  - 仅负责创建 `FastAPI` 实例、注册中间件、挂载静态文件、引入路由模块。
  - 不再直接包含业务逻辑；业务逻辑下沉到 `web/routes/`。
  - 保留对 `features.solver.service.generate_for_problem` 的**延迟导入**，避免 API 启动时强依赖 langgraph/langchain-ollama。

- `web/routes/problems.py` / `go_code.py` / `meta.py`
  - 分别处理 `/api/problems/*`、`/api/go-code/*`、`/health`、`/api/stats`。
  - 从 `features.problems.service` 调用拉题/查询能力。

### 4.2 features 层

#### problems

- `features/problems/client.py`
  - 包含 `GRAPHQL_URL`、`_HEADERS`、`_graphql`、`fetch_problem_list`、`fetch_problem_detail`。

- `features/problems/models.py`
  - 包含 `normalize_problem`、`render_problem_markdown`、`_go_template`、`_zero`、`_slugify_filename`。

- `features/problems/storage.py`
  - 包含 `save_problem`、`save_index`、`save_index_json`、`load_problem_file`、`_summarize`。

- `features/problems/service.py`
  - 包含 `enrich_problem_set`、`resolve_problem`、`find_local_problem`、`list_local_problems`、`problem_to_input`。
  - 默认 `DEFAULT_OUTPUT_DIR` 从 `infrastructure.paths` 读取。

- `features/problems/example/main.py`
  - 迁移当前 `problems.py` 底部的 CLI 逻辑。
  - 迁移 `debug/trial_problems.py` 的测试 harness（或合并为一个带 `--trial` 的 CLI）。

#### solver

- `features/solver/state.py`
  - 迁移当前 `state.py` 内容。

- `features/solver/nodes.py`
  - 迁移当前 `nodes.py` 内容。
  - 将 `BASE_DIR / "output" / "go-code"` 的硬编码路径改为从 `state[StateKey.TASK_DIR]` 与配置化输出目录组合，由 `executor.py` 负责拼接。

- `features/solver/executor.py`
  - 从 `code_executor_node` 中抽出：
    - 提取 Go 代码块
    - 写入文件
    - 执行 `go fmt`
    - 执行 `go build`
  - 提供 `execute_go_code(code: str, task_name: str, output_dir: Path) -> dict`。

- `features/solver/workflow.py`
  - 迁移当前 `workflow.py` 内容。
  - 导入 `executor.execute_go_code` 替换节点内联的编译逻辑。

- `features/solver/service.py`
  - 迁移 `main.py` 中的 `run_pipeline`、`generate_for_problem`。
  - 提供统一的解题接口供 web 层调用。

- `features/solver/example/main.py`
  - 迁移当前 `main.py` 底部的 CLI 逻辑。
  - 支持 `--problem`、`-f`、`-c`、`--list-problems` 等参数。

### 4.3 infrastructure 层

- `infrastructure/paths.py`（新增）
  - `PROJECT_ROOT = Path(__file__).resolve().parent.parent`
  - `PROMPT_DIR = PROJECT_ROOT / "prompts"`
  - `MODEL_CONFIG_PATH = PROJECT_ROOT / "infrastructure" / "models.yaml"`
  - `DEFAULT_PROBLEMS_DIR = PROJECT_ROOT / "output" / "problems"`
  - `DEFAULT_GO_CODE_DIR = PROJECT_ROOT / "output" / "go-code"`

- `infrastructure/logger.py`
  - 迁移当前 `logger.py` 内容，**不依赖任何其他内部模块**。

- `infrastructure/constants.py`
  - 迁移当前 `constants.py` 中全局使用的部分（`StateKey`、`NodeName`、`PromptKey`、`Category`、`BUILD_SUCCESS_MESSAGE` 等）。
  - 若某些常量仅 solver 内部使用，可保留在 `features/solver/constants.py`。

- `infrastructure/config.py`
  - 迁移当前 `config.py` 内容。
  - 将 `BASE_DIR`/`PROMPT_DIR`/`MODEL_CONFIG_PATH` 改为从 `infrastructure.paths` 导入。
  - 导入 `infrastructure.logger`。

- `infrastructure/models.yaml`
  - 从根目录移入 `infrastructure/`。

### 4.4 清理根目录

- 删除旧的 `main.py`、`api.py`、`problems.py`、`workflow.py`、`nodes.py`、`state.py`、`config.py`、`constants.py`、`logger.py`。
- 将 `models.yaml` 移动到 `infrastructure/models.yaml`。
- 保留 `prompts/`、`frontend/`、`output/`、`requirements.txt`、`README.md`。

## 5. 导入关系图（目标）

```
web.main
└── web.api
    ├── web.routes.meta
    ├── web.routes.problems
    │   ├── features.problems.service
    │   │   ├── features.problems.client
    │   │   ├── features.problems.models
    │   │   ├── features.problems.storage
    │   │   └── infrastructure.paths
    │   └── infrastructure.logger
    ├── web.routes.go_code
    │   ├── infrastructure.paths
    │   └── infrastructure.logger
    └── web.routes.problems.generate (延迟)
        └── features.solver.service
            ├── features.solver.workflow
            │   ├── features.solver.nodes
            │   │   ├── infrastructure.config
            │   │   ├── infrastructure.constants
            │   │   ├── infrastructure.logger
            │   │   └── features.solver.state
            │   ├── features.solver.executor
            │   └── infrastructure.logger
            └── features.problems.service

infrastructure.config
└── infrastructure.logger

infrastructure.constants  （无内部依赖）
infrastructure.logger   （无内部依赖）
infrastructure.paths    （仅 pathlib，无内部依赖）
```

## 6. 分阶段执行步骤

### Phase 1：基础设施独立

1. 创建 `infrastructure/paths.py`，定义统一路径。
2. 创建 `infrastructure/__init__.py`。
3. 将 `logger.py` 原样迁移到 `infrastructure/logger.py`。
4. 将 `constants.py` 原样迁移到 `infrastructure/constants.py`。
5. 将 `models.yaml` 迁移到 `infrastructure/models.yaml`。
6. 将 `config.py` 迁移到 `infrastructure/config.py`，并替换 `BASE_DIR`/`PROMPT_DIR`/`MODEL_CONFIG_PATH` 为从 `infrastructure.paths` 导入。

### Phase 2：拆分 problems 功能包

1. 创建 `features/problems/` 及子目录。
2. 按职责拆分 `problems.py`：
   - GraphQL 相关 → `client.py`
   - normalize/render → `models.py`
   - save/load/index → `storage.py`
   - `resolve_problem`、`enrich_problem_set`、`problem_to_input` → `service.py`
3. `service.py` 的默认输出目录改用 `infrastructure.paths.DEFAULT_PROBLEMS_DIR`。
4. 将 `problems.py` 的 CLI 逻辑迁移到 `features/problems/example/main.py`。
5. 将 `debug/trial_problems.py` 迁移到 `features/problems/example/trial_problems.py`，并调整 `ROOT` 计算方式。
6. 验证：`python -m features.problems.example.main --limit 5` 可正常拉题。

### Phase 3：拆分 solver 功能包

1. 创建 `features/solver/` 及子目录。
2. 迁移 `state.py` → `features/solver/state.py`。
3. 新增 `features/solver/executor.py`，将 `code_executor_node` 中的文件写入与编译逻辑抽出。
4. 迁移 `nodes.py` → `features/solver/nodes.py`，使用 `executor.execute_go_code`。
5. 迁移 `workflow.py` → `features/solver/workflow.py`。
6. 新增 `features/solver/service.py`，包含：
   - `run_pipeline(...)`
   - `generate_for_problem(...)`
7. 将 `main.py` 的 CLI 逻辑迁移到 `features/solver/example/main.py`。
8. 验证：`python -m features.solver.example.main --problem two-sum` 可正常生成代码。

### Phase 4：搭建 web 主线

1. 创建 `web/` 目录及子目录。
2. 将 `api.py` 中的 Pydantic 模型抽到 `web/schemas.py`。
3. 将路由拆分到 `web/routes/` 下三个模块。
4. 在 `web/api.py` 中注册路由、中间件、静态文件。
5. 新建 `web/main.py` 作为 uvicorn 入口。
6. `generate` 路由保留对 `features.solver.service.generate_for_problem` 的延迟导入。
7. 验证：`uvicorn web.main:app --reload` 启动正常，前端页面可访问。

### Phase 5：清理与文档

1. 删除根目录下旧文件：`main.py`、`api.py`、`problems.py`、`workflow.py`、`nodes.py`、`state.py`、`config.py`、`constants.py`、`logger.py`。
2. 删除或合并 `debug/` 目录（内容已迁移到 `features/problems/example/`）。
3. 更新 `README.md`：
   - 生产启动命令改为 `uvicorn web.main:app --reload --port 8000`。
   - 新增功能包测试命令：`python -m features.problems.example.main`、`python -m features.solver.example.main`。
   - 更新项目结构图。
4. 更新 `requirements.txt`（依赖不变，必要时补充 `fastapi`、`uvicorn` 已存在）。

## 7. 注意事项

1. **路径问题**：所有内部模块应统一从 `infrastructure.paths` 获取项目根目录，禁止继续使用 `Path(__file__).resolve().parent` 在业务模块中推导路径。
2. **延迟导入保留**：`web` 层启动时不应因为缺少 langgraph/langchain-ollama 而失败；仅在调用 `/api/problems/{identifier}/generate` 时才导入 `features.solver.service`。
3. **循环导入**：
   - `infrastructure.config` 只导入 `infrastructure.logger`，不导入业务模块。
   - `features.solver.nodes` 导入 `infrastructure.config`，不反向导入。
   - `features.solver.service` 可以导入 `features.problems.service`，但 `problems` 不应反向依赖 `solver`。
4. **测试验证**：每完成一个 Phase 都要运行对应 example 入口，确认行为一致后再进入下一阶段。
5. **保留提交历史**：所有改动通过普通 `git mv` + `git commit` 完成，保留文件历史便于回滚。
6. **输出目录不变**：`output/problems/` 和 `output/go-code/` 继续保留在原位置，避免已生成的历史数据失效。

## 8. 验证清单

- [ ] `python -m infrastructure.config` 能正确加载 `models.yaml` 和 prompts。
- [ ] `python -m features.problems.example.main --limit 5` 能拉取并保存题目。
- [ ] `python -m features.problems.example.trial_problems 5` 能完成 trial 测试。
- [ ] `python -m features.solver.example.main --problem two-sum` 能生成并编译 Go 代码。
- [ ] `uvicorn web.main:app --reload` 能正常启动服务。
- [ ] 访问 `http://localhost:8000/ui/` 前端正常。
- [ ] `POST /api/problems/pull`、`POST /api/problems/{slug}/generate`、`GET /api/go-code` 等接口正常。
