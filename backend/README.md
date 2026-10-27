# TwinMarket Backend

`backend/` 是独立的 FastAPI 控制和查询面。它管理 Run Registry、后台 Worker、RuntimeEvent/SSE、结果产物，以及 Studio 所需的只读 catalog、文档、Agent、Market 和日志接口；后端生产代码不依赖 `studio.*`。

## 启动

从仓库根目录安装后端和模拟运行依赖：

```sh
venv/bin/python -m pip install -r backend/requirements.txt
venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8766
```

开发中的 Studio 前端仍把 `/api` 代理到 `127.0.0.1:8766`，因此 API 路径、响应结构和错误状态保持不变。

## 配置

以下环境变量由 HTTP 服务与它启动的 Worker 共同使用。相对路径以 `TWINMARKET_PROJECT_ROOT` 为基准；项目根本身的相对值以启动时的当前目录为基准。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `TWINMARKET_PROJECT_ROOT` | 仓库根目录 | 模拟代码、配置和文档根目录 |
| `TWINMARKET_DATA_DIR` | `data/` | 后端读取的模板、市场与索引数据目录 |
| `TWINMARKET_RUNS_DIR` | `runs/` | 新运行的独占输出目录 |
| `TWINMARKET_REGISTRY_PATH` | `backend/data/runs.sqlite3` | SQLite Run Registry 文件 |
| `TWINMARKET_ALLOWED_ORIGINS` | `http://127.0.0.1:5173` | 逗号或换行分隔的 CORS 前端 origin 列表 |

例如：

```sh
export TWINMARKET_PROJECT_ROOT=/srv/twinmarket
export TWINMARKET_DATA_DIR=/srv/twinmarket/data
export TWINMARKET_RUNS_DIR=/var/lib/twinmarket/runs
export TWINMARKET_REGISTRY_PATH=/var/lib/twinmarket/runs.sqlite3
export TWINMARKET_ALLOWED_ORIGINS=https://studio.example.test
venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8766
```

如果需要继续查看迁移前的本地草稿，可把 `TWINMARKET_REGISTRY_PATH` 指向旧的 `studio/backend/data/runs.sqlite3`；该文件不会由迁移自动删除。

## 验证

```sh
./script/check.sh
```

该命令分别检查核心仿真、独立后端和 Studio 前端。

## 市场数据

行情同步、Tushare 凭据、数据版本和市场模板已迁到独立的市场数据服务（`python -m market_service`，默认端口 8100），见 [设计文档](../docs/MARKET_DATA_SERVICE_DESIGN_ZH.md)。本后端不再调用 Tushare：创建模板模式的 Run 时，经 `TWINMARKET_MDS_URL` 请求覆盖检查和数据包，把数据包下载到 `runs/<run_id>/market/` 并校验指纹；之后启动和续跑都不依赖该服务。设置了 `TWINMARKET_MDS_TOKEN` 时，请求会带上该令牌。旧的快照模式 Run 仍可按其清单校验后启动，但不能再新建。运行接口测试可安装 `backend/requirements-dev.txt`。
