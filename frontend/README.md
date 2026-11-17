# TwinMarket Studio

> Studio 是当前持续开发的研究工作台前端。P0 聚焦真实本地仿真的最小闭环：配置模拟 → 启动 Run → 观察 Runtime → 查看真实结果。

四个主路由：`/studio/Experiment`、`/studio/Agent`、`/studio/Market`、`/studio/Setting`。

独立于仓库根目录的论文主页。Studio 现在使用真实本地运行目录创建草稿、派发独立模拟 Worker、读取结构化 RuntimeEvent 与查询真实产物；它不生成示例实验、虚构实时价格或测试分数。

## 前后端边界

- `src/`：Studio 四空间壳层与各页面；`src/experiments/`：实验列表、新建实验和实验工作台。
- `../backend/server/`：独立 FastAPI 控制/查询面、SQLite Run Registry、Process Manager 与 Worker。
- `../backend/simulation/runtime/`：与 FastAPI/React 解耦的 RuntimeEvent、EventStore、Observer 和 Snapshot Reducer。
- `../backend/runs/<run_id>/`：每个运行独占的配置快照、`events.jsonl`、`state.json`、数据库、日志和结果产物。

## 本地运行

前端代理 `/api` 到 `127.0.0.1:8766` 的 FastAPI 控制面：

```sh
cd /Users/fitz/Projects/TwinMarket/backend
.venv/bin/python -m uvicorn server.app.main:app --host 127.0.0.1 --port 8766
```

```sh
cd /Users/fitz/Projects/TwinMarket/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

Python 3.10+，Node 18+；浏览器自动化测试使用本机已安装的 Google Chrome。`npm run build` 验证前端构建；没有配置生产 Python API 部署，不能仅发布静态文件就得到数据服务。

## 可验证体验

- 实验列表（`/studio/Experiment`）：表格/卡片两种视图、可选列（创建/更新时间、快照、Worker、种子、Run ID）、搜索与状态筛选、行内运行/停止/续跑，复制、归档与删除草稿收在“更多”菜单。
- 新建实验（`/studio/Experiment/new`，`?from=<run_id>` 以已有实验为模板）：独立页面，带面包屑；取消返回来源页，有未保存修改时离开需确认，创建成功后以工作台替换当前历史记录。
- 实验工作台（`/studio/runs/<run_id>/<tab>`）：运行与结果合并为一页，标签为运行进度、Agents、结果、日志与诊断、配置；已完成的实验默认打开结果。
- Agent：智能体空间与工作台共用 Agent 浏览器（表格 + 右侧详情分栏：决策流水线、LLM 上下文、档案与持仓）；行为金融测试仅预留流程入口。
- Market：环境预览、现有撮合机制、行业指数包、本地行情/新闻/索引库存。
- Setting：模型名称与配置文件存在性、数据源接入规划、浏览器本地密度偏好；不自动测试模型或外部连接。
- 舒适/紧凑保留相同信息，改变留白与行高；断连保留上次数据，错误提示与日志陈旧提示相互独立。

## 数据口径与边界

- `logs_*` 目录暂按一实验/一运行导入；没有编造 Experiment、Group 或 Environment 版本注册表。
- 独立后端的只读服务仍使用 `mode=ro` + `PRAGMA query_only=ON`；新 Registry 默认只写入 `backend/server/data/` 与当前 `backend/runs/<run_id>/`，不写既有 `logs_*`。这些位置可通过后端环境变量覆盖。
- 仅绑定 loopback；禁止将这个无鉴权的开发服务转发到公网。Vite 开发代理把 `/api` 指向本机 FastAPI。
- 配置只输出白名单参数、模型名称和文件存在性；日志仅抽取正则匹配的运行事件，不返回原始响应、凭据或 HTTP 请求详情。PKL 只检查文件存在性与大小，不反序列化。
- “近期有更新”只表示日志文件新鲜，不表示进程存活。“已完成”必须检测到结束标记。120 秒未更新标记“日志未更新”，不自动判定失败。
- 日期格只在 Profiles 实际有该日记录时标记快照；任一成员出现该日快照不意味着所有成员/日流程完成。多文件读取并非跨文件事务快照。
- 本次交易为 `date_time >= start_date` 的记录数；初始化历史另外显示。未识别来源时显示缺失而非伪造 0。
- 资产价格含初始化历史，使用真实收盘价折线；不画缺少数据支撑的 K 线。资产包权重不等于成员实际组合权重。
- Agent Group、Market Environment、外部连接管理仍属于 P1；P0 只允许新建运行所需的非敏感参数，模型密钥不从 API 返回。
- 当前数据规模下采用 20 条服务端分页和最多 180 个行情点；没有并发写入压力测试或长期监控可靠性验证。

## 验证

```sh
cd backend
scripts/check.sh
```

浏览器检查需先用 `backend/scripts/dev.sh` 启动后端与 Studio，并使用本机 Google Chrome：

```sh
npm run qa:smoke    # 断言检查
npm run qa:capture  # 1440/1024/390 三种宽度截图
```

`qa:smoke` 覆盖实验列表（列设置持久化、表格/卡片切换、搜索与状态筛选）、新建实验（面包屑、取消返回、深链接回退、`?from=` 预填、未保存离开确认）、实验工作台（按 URL 切换 tab、Agent 详情分栏）、智能体空间复用 Agent 浏览器、行情曲线/数据切换、文档安全与撤回、断连保留与恢复、三种宽度无横向溢出与移动端底部导航，以及无前端写请求。截图写入 `qa/radix/`；`qa/` 根目录下的旧截图与 `design-qa.md` 记录的是 Animal Island 版界面。

## 组件与许可证

React 19 + Vite；Radix Themes 3.2.1（MIT）与 Radix Icons；ECharts 6.1.0。路由为基于 History API 的轻量实现（`src/router.jsx`）。

按钮规范：主操作 `solid`（每个区域最多一个），次操作 `soft gray`，停止/删除 `soft red`，行内操作 `ghost`；视图与筛选切换用 `SegmentedControl` 或 `Tabs`，不用主按钮表示选中态。

早期原型基于 Animal Island UI，对应设计稿 `../docs/UI_DESIGN_ANIMAL_ISLAND_ZH.md` 已不再是当前视觉来源。
