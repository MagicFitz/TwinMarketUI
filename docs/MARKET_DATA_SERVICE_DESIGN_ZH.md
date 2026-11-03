# 市场数据服务与市场模板设计

本文给出新的数据架构：把市场数据从仿真后端中拆出来，做成独立的 **市场数据服务（Market Data Service，下称 MDS）**，负责日常更新和预处理，新闻是其中的一类数据。仿真后端不再自己同步数据：创建仿真时选择一个 **市场模板**，到需要数据时按模板向 MDS 拉取一个冻结的 **数据包（bundle）**。

本文替代早先的新闻快照方案。第一节是对现状的检查结果，其余各节是新设计。

---

## 一、现状

### 1.1 新闻数据

| 资产 | 内容 | 问题 |
| --- | --- | --- |
| `data/sorted_impact_news.pkl` | `cal_date, news[list[str]]`，550 天（2023-06-15 → 2024-12-15），共 3439 条，日均中位数 3 条，最多 133 条 | 只有正文字符串，没有标题、来源、发布时间、排序分数。有近似重复（如“【标题】正文”与正文本身）。排序方法不可追溯 |
| `data/InformationDB_news_2023/` | `IndexFlatL2`，3439 个向量，类型全部是 `daily_news` | 由同一个 pkl 构建，没有记录嵌入模型和维度。使用 pickle 序列化 |
| `util/InformationDB.py` 中的公告、新闻联播、长篇、快讯 CSV 管线 | 支持四类数据入库 | 仓库里没有这些原始数据，也没有获取脚本 |

- **旧版模式**：top user 在 `_read_news()` 中读取当天全部新闻；非随机交易者通过 `_desire_agent()` 检索最近 7 天的新闻。
- **快照模式**：`market_sync.py` 写入全空的新闻文件，Agent 使用 `EmptyNewsIndex`，因此没有任何新闻。当前的默认快照覆盖 2023-01 到 2026-10，跑在上面的实验都没有新闻。
- **Studio**：没有查看新闻的入口，只有“允许无新闻模式”的复选框。

### 1.2 行情与规则

- 行情同步（`backend/market_data/*`、`services/market_sync.py`、`market_worker.py`）和 Tushare 凭据都放在仿真后端里，与 Run 管理共用一个进程和一个 registry 目录。
- 市场规则写死在仿真代码中：
  - 涨跌停 ±10%：`trading_agent._get_price_limits`、`matching_engine.calculate_closing_price`；
  - 交易数量是 100 股的整数倍；
  - 每日一次集合撮合；
  - 由交易日历决定当天是否开市，非交易日走 `*_holiday` 分支；
  - 至少需要 30 个先前交易日（MA30）；
  - 成分股停牌时当日收益计 0，有效权重低于 80% 时阻止发布。

  这些规则既不可配置，也没有进入 Run 清单。

### 1.3 问题

1. **前视偏差**：新闻只精确到日期，D 日的新闻在 D 日开盘前就发给 Agent；检索的截止日期也包含当天。
2. **嵌入模型没有固定**：查询用运行时配置的模型，与建索引时的模型可能不同，检索失败时静默返回空。
3. **Bug**：LLM 没给出查询词时，`_desire_agent()` 返回 `""`，调用方按两个值解包，会抛出 `ValueError`，这条调用链上没有异常处理（`trader/trading_agent.py:434`）。
4. **检索效率**：先全量检索再按日期过滤，数据量大了会变慢。
5. **可复现性**：Run 清单里没有索引、嵌入模型和市场规则。
6. **耦合**：数据同步要等仿真后端在线，仿真后端也持有 Tushare Token。数据只有在人手动点“更新数据”时才会更新。

---

## 二、目标架构

```
┌──────────────────────────── 市场数据服务 MDS（独立进程 :8100）────────────────────────────┐
│  调度器 ──► 采集任务（Tushare：行情 / 估值 / 日历 / 新闻 / 公告）                           │
│                │                                                                      │
│                ▼                                                                      │
│  原始层 raw ──► 规整层 curated ──► 派生层 derived（指数、均线、新闻去重、排序、嵌入）          │
│                                    │                                                  │
│  市场模板 templates（版本化）────────┼──► 计划 plan（覆盖检查） ──► 数据包 bundle（冻结、只读）│
└────────────────────────────────────┼──────────────────────────────────────────────────┘
                                     │  HTTP：plan / bundle / 文件下载
┌────────────────────────────────────▼─────────── 仿真后端（:8766）────────────────────────┐
│  Run 创建：选择模板 + 日期 ──► 请求 bundle ──► 下载到 runs/<id>/market/ 并校验 SHA-256      │
│  Worker：只读取 Run 目录中的 bundle 和 rules.json，不再访问 MDS 或 Tushare                │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

**职责划分**

| | MDS | 仿真后端 |
| --- | --- | --- |
| 持有 | Tushare Token、嵌入模型（用于新闻预处理）、数据仓库、模板 | Run registry、Agent 模板、语言模型配置 |
| 运行 | 定时采集、预处理、质量校验、打包 | 准备 Run、启动或续跑 Worker、展示结果 |
| 不做 | 不知道 Run 和 Agent | 不调用 Tushare，不改写市场数据 |

**关键性质**

- Run 一旦拿到 bundle 就自给自足。MDS 停机、升级或数据修订，都不影响正在运行或续跑的 Run。
- 同一个模板版本、日期范围和数据版本，总是得到同一个 bundle（内容寻址），可以复现，也可以复用缓存。

---

## 三、MDS 数据层

### 3.1 目录

```
$TWINMARKET_MDS_HOME/              # 默认 ./mds_data，git 忽略
  mds.sqlite3                      # 任务、调度、模板、数据版本、bundle 登记
  warehouse.sqlite3                # 规整层与派生层（行情、指数、日历、新闻、嵌入）
  raw/<dataset>/<yyyy>/<job_id>.jsonl.gz   # 原始成功响应 + request_manifest
  vectors/<model_id>/news.f32      # 新闻向量（按 article 行号追加）
  bundles/<bundle_id>/             # 已冻结的数据包，只读
```

数据量估计：50 只成分股 × 每年 250 天，加上每年约 10 万条新闻，SQLite 足以应对，不需要引入 Parquet 或 DuckDB 等新依赖。

### 3.2 数据集与版本

每个数据集都登记在 `datasets` 表中，记录 `dataset_id`、`through`（已就绪到哪天）、`revision`、`quality`。每次采集或预处理成功后，生成一个全局递增的 **数据版本** `data_version`：

```sql
CREATE TABLE data_versions (
  data_version INTEGER PRIMARY KEY, created_at TEXT, job_id TEXT,
  changes TEXT);                     -- JSON：{dataset: {from, to, rows}}
```

规整层的每张表都带 `valid_from_version` 和 `valid_to_version` 两列（供应商修订时把旧行关闭，再插入新行）。这样任意一个 `data_version` 都能还原出当时的数据视图，bundle 也只需要记录它基于哪个 `data_version`。

| 数据集 | 层 | 主键 | 来源 |
| --- | --- | --- | --- |
| `calendar` | curated | exchange, cal_date | `trade_cal` |
| `equity_daily` | curated | ts_code, trade_date | `daily` |
| `equity_valuation` | curated | ts_code, trade_date | `daily_basic` |
| `moneyflow` | curated | ts_code, trade_date | `moneyflow`（后续） |
| `index_daily` | derived | asset_definition, ts_code, date | 现有 `build_indices` 逻辑，按资产定义版本区分 |
| `news_articles` | curated | article_id | `news`、`major_news`、`cctv_news`、`anns_d`、旧数据导入、手工导入 |
| `news_clusters` | derived | cluster_id | simhash 近似去重 |
| `news_tags` | derived | article_id, asset_code | 成分股名称、代码和行业关键词 |
| `news_scores` | derived | article_id, method | rule-v1 规则分 / llm-v1 打分缓存 |
| `news_embeddings` | derived | content_sha256, model_id | 预先计算，跨 bundle 复用 |

`news_articles` 的关键字段：`source, channel, published_at（+08:00）, time_precision（minute|day）, title, content, content_sha256, url, ts_codes, synthetic`。另外建 FTS5（trigram）全文索引，供前端检索。

### 3.3 日常调度

调度器是 MDS 进程内的轻量循环，调度表保存在 `schedules` 表中，可以在前端开关和修改，不引入额外依赖。所有写任务共用一把写锁，同一时间只有一个任务调用 Tushare，避免互相挤占限流额度。

| 任务 | 默认时间（Asia/Shanghai） | 内容 |
| --- | --- | --- |
| `news_intraday` | 07:00–23:30 每 30 分钟 | 拉取 `news` 快讯的增量（按时间窗口分页，重扫最近 2 小时） |
| `news_daily` | 每天 20:30 | `major_news`、`cctv_news`、`anns_d`；对前一天做补扫 |
| `market_daily` | 交易日 18:45 | `trade_cal`、`daily`、`daily_basic`；重拉最近 3 个交易日，修订进入新版本 |
| `derive` | 每次采集成功后 | 计算指数和均线；新闻去重、打标签、规则打分、计算嵌入（只算新文章） |
| `quality` | 每天 21:00 | 覆盖缺口、异常价格、新闻空白时段；结果写入健康状态，前端显示告警 |

手动触发、日期回补和取消沿用现有同步任务的机制（Worker 子进程、持久化进度、在请求之间检查取消）。

### 3.4 新闻预处理要点

- **时间**：每条新闻都有 `published_at`。`cctv_news` 统一设为当日 19:00；公告设为公告日 00:00，并标为日精度；旧数据导入同样标为日精度，并带警告。
- **去重**：先去掉开头的【标题】、统一全半角，然后用 `content_sha256` 去掉完全重复；再用 simhash（汉明距离 ≤ 3）把近似重复归入同一簇。簇的大小也是重要性信号之一。
- **排序**：`rule-v1` 按来源权重、簇大小、是否命中资产或宏观关键词打分；`llm-v1` 可选，结果缓存。分数提前算好，打包时只需按窗口取 Top K。
- **嵌入**：MDS 为“已启用的嵌入模型”预先计算向量，模板只能引用已启用的模型。`model_id` 由 provider、model 和 base_url 的哈希组成，不包含密钥。

---

## 四、市场模板

### 4.1 定义

模板是版本化的 YAML，存放在 MDS 中（`templates` 表保存每个版本的完整内容和 SHA-256）。模板发布后不可修改，改动只能发布为新版本。Run 固定使用 `template_id@version`。

```yaml
template_id: cn-a-industry10
version: 3
name: A 股 10 行业指数 · 日频
description: 现有 50 只成分股合成的 10 个自定义指数，收盘后集合撮合

universe:
  asset_definition: market_assets.yaml@701033c1     # 资产定义文件及其哈希
  instruments: industry_indices                     # 交易标的是 10 个指数

data:                                               # 数据清单
  calendar:   {exchange: SSE, required: true}
  bars:       {dataset: index_daily, fields: [open, high, low, close, pre_close, pct_chg, vol, amount], required: true}
  indicators: {ma: [5, 10, 30], vol_ma: [5, 10, 30]}
  valuation:  {dataset: equity_valuation, required: false, on_missing: null_fill}
  moneyflow:  {required: false, on_missing: null_fill}
  history:    {lookback_trading_days: 30}           # MA30 需要
  news:
    required: true                                  # false 时允许无新闻
    sources: [tushare.news, tushare.major_news, tushare.cctv_news]
    include_synthetic: false
    broadcast: {ranking: rule-v1, top_k: 10}
    retrieval: {window_days: 7, top_k: 2, embedding_model: "openai:text-embedding-3-small"}
    min_daily_articles: 1                           # 低于此数的日期记为空白
    on_gap: warn                                    # warn | block

session:                                            # 开市与闭市规则
  timezone: Asia/Shanghai
  trading_days: calendar                            # 按交易日历判断是否开市
  information_cutoff: "09:00"                       # D 日决策可见的最晚信息时刻
  decision_time: "09:15"
  matching: {mode: daily_call_auction, price: volume_maximizing}
  close_time: "15:00"
  non_trading_day: {forum: true, trading: false}    # 对应现在的 holiday 分支

rules:
  price_limit_pct: 10
  lot_size: 100
  settlement: T+1
  fees: {commission_bps: 0, stamp_duty_bps: 0}
  missing_constituent: zero_return                  # 停牌时收益计 0
  min_weight_coverage: {block: 0.80, warn: 0.95}
```

### 4.2 内置模板

| 模板 | 用途 |
| --- | --- |
| `cn-a-industry10`（含新闻） | 新实验的默认模板 |
| `cn-a-industry10-nonews` | 对照实验：`news.required: false, sources: []` |
| `legacy-2023` | 旧版数据：导入 `stock_data.csv` 和 `sorted_impact_news.pkl`，固定为 2023-06-15 → 2024-12-15，用于复现论文实验 |

模板编辑器允许在内置模板的基础上派生新模板。比较常见的改动包括涨跌停幅度、信息截止时刻、新闻来源和 Top K，以及是否注入情景新闻。

### 4.3 仿真侧如何使用规则

bundle 中会带一份 `rules.json`，内容是模板 `session` 与 `rules` 两节展开后的结果。仿真代码改为从这里读取规则，不再写死：

- `_get_price_limits` 和 `calculate_closing_price` 读取 `price_limit_pct`；
- 下单数量取整读取 `lot_size`；
- `simulation.py` 用 `information_cutoff` 计算每天的新闻窗口和检索上界；
- `non_trading_day` 决定非交易日的分支；
- 模板中的 `settlement` 和 `fees` 如果是现有代码不支持的取值（例如 T+0、非零费率），MDS 照样可以保存，但仿真后端在创建 Run 时会拒绝，返回 `RULE_UNSUPPORTED`，并说明哪一项不支持。这样模板语义能逐步扩展，同时不会悄悄被忽略。

---

## 五、计划与数据包

### 5.1 流程

1. **计划（plan，只读）**：`POST /v1/plans {template, start_date, end_date}`。MDS 按模板逐项检查覆盖情况，包括交易日、回看历史和新闻的检索窗口，返回：

   ```json
   {"ok": false, "data_version": 1842,
    "checks": [
      {"item": "bars", "status": "ok", "through": "2026-10-23"},
      {"item": "history", "status": "ok", "lookback": 30},
      {"item": "news", "status": "warn", "gaps": ["2025-02-01"], "time_precision_day_ratio": 0.0},
      {"item": "rules.settlement", "status": "ok"}],
    "estimate": {"bytes": 12400000, "news_articles": 3812}}
   ```

2. **打包（bundle）**：Run 从 draft 进入 queued 时，仿真后端调用 `POST /v1/bundles`，参数是模板、日期和计划中的 `data_version`。MDS 在该版本的数据视图上生成 bundle：

   ```
   bundles/<bundle_id>/
     bundle.json                 # 模板全文与哈希、data_version、日期、检查结果、警告、文件指纹
     rules.json
     stock_data.csv              # 兼容现有仿真代码
     trading_days.csv
     stock_profile.csv
     market_assets.yaml
     news/articles.jsonl.gz      # 窗口内的文章（含 published_at、来源、标签）
     news/sorted_impact_news.pkl # 兼容文件：每个模拟日按 cutoff 窗口取 Top K
     news/index.faiss            # 余弦相似度，行按 published_at 升序
     news/index_rows.jsonl.gz
   ```

   `bundle_id = "b-" + sha256(template_sha, start, end, data_version)[:20]`，相同的请求直接返回已有的 bundle。

3. **拉取**：仿真后端用 `GET /v1/bundles/{id}/archive`（tar.zst 或 tar.gz）下载到 `runs/<run_id>/market/`，逐个文件校验 SHA-256，然后设为只读。manifest 记录 `market_template`、`bundle_id`、`data_version` 和文件指纹。
4. **运行**：Worker 把 `TWINMARKET_DATA_DIR` 指向 `runs/<run_id>/market/`，再用 `TWINMARKET_RULES` 指向 `rules.json`。续跑时只重新校验指纹，不再访问 MDS。

### 5.2 时间语义（防前视）

对模拟日 D：

- **广播窗口**：`(cutoff(D_prev), cutoff(D)]`，其中 `D_prev` 是上一个模拟日。
- **检索范围**：`[cutoff(D) − window_days, cutoff(D)]`。
- **行情**：Agent 只能观察到 D 之前的收盘数据（现有规则不变）。

打包时会验证：任何一天的广播和检索范围内，都不能出现 `published_at > cutoff(D)` 的文章。日精度的文章按当日 00:00 计入，并在 `bundle.json` 中给出警告。

### 5.3 bundle 的生命周期

- MDS 记录每个 bundle 被哪些 Run 引用。仿真后端在 Run 删除或归档时通知 MDS，引用数归零的 bundle 可以由 GC 清理。
- 仿真后端也可以只保留 Run 目录中的副本。Run 自给自足，因此 MDS 端的 bundle 只是缓存。

---

## 六、接口

### 6.1 MDS（`/v1`，Studio 和仿真后端共用）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/v1/health` | 各数据集的 `through`、最近任务、告警 |
| GET/PUT | `/v1/sources/tushare`，POST `…/test` | 凭据与各接口权限探测（从仿真后端迁移过来） |
| GET/PUT | `/v1/schedules`，POST `/v1/schedules/{id}/run` | 调度开关、时间、立即运行 |
| GET/POST | `/v1/jobs`，GET `/{id}`，POST `/{id}/cancel` | 采集与回补任务 |
| GET | `/v1/datasets`，`/v1/datasets/{id}/coverage?from&to` | 覆盖日历 |
| GET | `/v1/market/series?asset&from&to&data_version` | 行情浏览（替代现在的 `/snapshots/{id}/market`） |
| GET | `/v1/news?from&to&source&asset&q&cursor`，`/v1/news/{id}` | 新闻浏览、全文检索、同簇报道 |
| POST | `/v1/news/imports` | 旧数据或情景新闻导入（JSONL/CSV） |
| GET | `/v1/news/day/{date}?template=` | 按模板规则预览某天的广播 Top K 和分数 |
| POST | `/v1/news/search` | `{template, date, query}`，按 Agent 的规则试跑检索 |
| GET/POST | `/v1/templates`，`/v1/templates/{id}/versions` | 模板列表、版本、发布新版本（服务端校验 schema） |
| POST | `/v1/plans` | 覆盖检查 |
| POST | `/v1/bundles`，GET `/{id}`，`/{id}/archive` | 打包与下载 |
| POST/DELETE | `/v1/bundles/{id}/refs/{run_id}` | 引用登记 |

鉴权：本机部署时只监听 127.0.0.1。仿真后端调用 MDS 时带 `TWINMARKET_MDS_TOKEN`（共享密钥），Studio 通过 Vite 代理 `/mds` 访问 MDS。

### 6.2 仿真后端的变化

- 删除 `/api/market/sync-jobs*`、`/api/settings/data-sources*`、`/api/market/snapshots*`。迁移期这些路径先保留为指向 MDS 的 307 跳转。
- `POST /api/runs` 的配置去掉 `market_snapshot_id` 和 `allow_missing_news`，改为 `market_template`（`id@version`）。可以附带计划中的 `data_version`，不附带时取最新版本。
- 新增 `GET /api/runs/{id}/market/news?date=`：从 Run 自己的 bundle 读取当天的广播，并结合 RuntimeEvent 给出各 Agent 的检索命中。

---

## 七、前端（Studio）

### 7.1 信息架构

把 **Market** 空间改成“市场数据服务”的控制台。仿真相关的页面只消费数据。

```
Market
├─ 概览       服务健康、各数据集就绪日期、最近任务、告警
├─ 行情       指数与成分股走势（现有 MarketChart），按 data_version 查看
├─ 新闻       覆盖日历 + 新闻浏览器 + 按日广播预览 + 检索试跑
├─ 模板       模板列表、版本对比、编辑器
└─ 任务与调度  调度开关、手动回补、任务进度
Setting → 数据源  Tushare 凭据与接口权限（请求发往 MDS）
```

### 7.2 页面要点

- **概览**：每个数据集一张卡片（日历、行情、估值、新闻快讯、长篇、新闻联播、公告），显示 `through`、最近更新时间和状态。MDS 不可达时整页显示离线提示，不影响 Run 列表。
- **新闻**：
  - 覆盖日历：每格表示一天的条数，空白时段标红，日精度的日期用斜纹表示。
  - 新闻浏览器：可按日期、来源、资产、关键词筛选；列表显示时间、来源徽章、标题、资产标签和“+N 家报道”；详情抽屉中可以看到正文、原文链接和同簇报道。
  - 按日预览：选择模板和日期，看到 top user 会读到的 Top K 和分数。
  - 检索试跑：输入查询，按模板的截止时刻和窗口返回结果。
- **模板**：
  - 列表：名称、最新版本、被多少个 Run 使用。
  - 编辑器：表单分为“数据清单”“新闻”“开闭市规则”“交易规则”几组，右侧是 YAML 预览。保存会发布为新版本，并显示与上一版的差异。
  - 仿真端不支持的规则在表单中标注“当前仿真不支持”。
- **任务与调度**：沿用现有同步任务卡片（阶段、进度、取消），新增调度表（开关、时间、下次运行时间），以及回补对话框（选择数据集和日期范围）。

### 7.3 新建实验

新建实验的流程改为三步：

1. **选择市场模板**：卡片显示模板名称、版本、数据清单摘要（是否含新闻、来源）和关键规则（涨跌停、截止时刻），可以展开查看完整模板。
2. **选择日期**：日期选择器的可选范围来自模板的覆盖区间。改动日期后，前端调用 `plans` 接口做实时检查，逐项显示 ok / warn / block。
   - 有 warn 时需要勾选“我已知悉”才能继续，例如新闻有空白日、含日精度新闻。
   - 有 block 时不能继续，并给出跳转链接，例如“前往任务页回补 2026-10-23”。
3. **其余参数**：与现在相同。

“允许无新闻模式”复选框删除，需要无新闻时选择 `-nonews` 模板即可。

Workspace / Run 详情中新增“市场环境”卡片，显示模板版本、bundle、data_version 和警告；另外新增“当日新闻”面板。

---

## 八、迁移

1. **代码搬迁**：`backend/market_data/*`、`services/market_sync.py`、`services/data_sources.py`、`market_worker.py` 移到新包 `market_service/`（FastAPI 应用，入口 `python -m market_service`）。`script/dev.sh` 同时启动三个进程。
2. **旧数据导入**：
   - 把 `data/market_snapshots/market-20261022-…` 中的 raw 和 normalized 数据导入仓库，作为最初的数据版本。
   - 把 `sorted_impact_news.pkl` 导入为 `source=legacy`、日精度的新闻。
   - 旧的 `stock_data.csv` 由 `legacy-2023` 模板引用。
3. **旧 Run 兼容**：已经引用 `market_snapshot_id` 的 Run 继续按原路径解析（只读，不再新建）；legacy 模式的 Run 不受影响。
4. **仿真代码改动**：读取 `rules.json`；`NewsIndex` 读取 bundle 中的索引，并固定嵌入模型；检索上界改为 `cutoff(D)`；修复 `_desire_agent` 的返回值。

---

## 九、分期

| 阶段 | 内容 | 完成标志 |
| --- | --- | --- |
| **P0 拆分** | 新建 `market_service`，搬迁行情同步与凭据；`warehouse.sqlite3` 与数据版本；导入现有快照和旧新闻；模板 v1（只支持现有规则的取值）；plan 与 bundle；仿真后端改为拉取 bundle；修复 `_desire_agent` | 用 `cn-a-industry10` 模板新建 Run，跑通 2 个交易日的烟雾测试，MDS 停机后续跑仍然成功 |
| **P1 新闻日常更新** | Tushare `news / major_news / cctv_news / anns_d` 采集；去重、打标签、rule-v1 排序、嵌入预计算；调度器；新闻页（覆盖日历、浏览器、按日预览）；新建实验的 plan 检查 | 调度连续运行一周没有缺口；新闻在 bundle 中通过防前视检查 |
| **P2 规则与研究能力** | `rules.json` 驱动涨跌停、整手和截止时刻；模板编辑器；llm-v1 排序；情景新闻注入；检索试跑；Run 侧的“当日新闻” | 用两个只在涨跌停幅度上不同的模板做对照实验 |

## 附：P0 实现状态（2026-10-26）

P0 已经实现，代码在 `market_service/`（服务）、`backend/market_bundle.py`（客户端）和 Studio 的 `MarketData.jsx`。下表列出与上文设计不同或推迟的地方。

| 项目 | 设计 | P0 实际 |
| --- | --- | --- |
| 打包时机 | Run 从 draft 进入 queued 时 | Run **创建时**打包（修改草稿时重新打包），因为草稿创建时就会写入 manifest；启动和续跑不访问 MDS |
| 每日广播排序 | 默认 `rule-v1` | 只实现了 `legacy-v1`：先按旧文件的当日顺序，再按发布时间排序，默认每日最多 20 条；`rule-v1` 放到 P1 |
| 新闻检索索引 | 打包 FAISS 索引，固定嵌入模型 | 推迟到 P1；模板的 `retrieval.enabled` 必须为 false，Agent 检索返回空，并作为警告写入 Run 清单 |
| 模板规则 | 包含 `settlement: T+1` | 去掉 settlement：现有撮合每天只有一次集合竞价，没有可配置的结算规则。仿真端只接受 ±10% 涨跌停、100 股一手、`daily_call_auction`、零费率，其他取值返回 `RULE_UNSUPPORTED` |
| `legacy-2023` 模板 | 内置 | 没有内置。旧版数据模式（`market_data_mode=legacy`）保留，用于精确复现论文实验；旧新闻已经作为 `legacy` 来源导入仓库，`cn-a-industry10` 模板会用到它 |
| 旧接口 | 迁移期 307 跳转 | 直接删除 `/api/market/*` 和 `/api/settings/data-sources*`：Studio 已改用 `/mds/v1`，跨源 307 对浏览器代理没有意义。旧的顶层 `frontend/` 应用曾调用这些路径，已于 2026-11-03 删除 |
| 旧快照 Run | — | 按 Run 清单中的文件指纹校验后，可以继续启动和续跑；不能新建，复制时会被拒绝 |
| 调度器、新闻采集、新闻页 | P1 | 未实现；`GET /v1/news` 浏览接口已经提供 |

运行方式：`script/dev.sh` 同时启动 MDS（8100）、仿真后端（8766）和 Studio。MDS 首次启动时，会把 `data/` 中最新的已发布快照（如果没有，就用旧 CSV）和 `sorted_impact_news.pkl` 导入 `mds_data/`，生成数据版本 1。

## 十、测试

- **MDS**：
  - 假 Tushare 响应：分页、权限降级、限流、重复页面直接失败；
  - 数据版本还原：修订前后的 bundle 内容各自稳定；
  - 调度器在重启后恢复；
  - 写锁保证任务互斥。
- **数据包**：
  - 相同输入得到相同 `bundle_id`；
  - 防前视断言；
  - 文件指纹被篡改时拒绝使用；
  - 模板中有仿真不支持的规则时返回 `RULE_UNSUPPORTED`。
- **仿真后端**：
  - 用假 MDS 测试 plan 和 bundle 的契约（JSON schema 固定在 `market_service/contract/`）；
  - MDS 离线时：已有 Run 可以续跑，新建 Run 返回明确的错误。
- **端到端**：1 个用户、2 个交易日、使用假模型，确认新闻广播和检索事件都已写入，manifest 中有模板和 bundle 信息。

## 十一、待确认

1. MDS 是否只在本机运行。如果以后要部署到服务器、供多台机器共享，鉴权和 bundle 下载需要再设计。
2. 信息截止时刻默认取 09:00 还是 09:15。
3. 快讯拉取的频率（30 分钟）是否合适。Tushare 新闻接口有单独的调用频次限制，需要用你的账户实测后确定。
