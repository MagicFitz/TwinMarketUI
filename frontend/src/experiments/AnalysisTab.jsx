import React, { useEffect, useRef, useState } from "react";
import { Button, Flex, Grid, SegmentedControl, Slider, Text } from "@radix-ui/themes";
import { DownloadIcon } from "@radix-ui/react-icons";
import { fetchJson, useData } from "../lib.js";
import { AgentDistribution } from "../agents/AgentDistribution.jsx";
import { AgentExplorer } from "../agents/AgentExplorer.jsx";
import { MarketChart } from "../MarketChart.jsx";
import { Empty, Panel, Stat, icon } from "../ui.jsx";
import { AgentLink, Chart, DataTable, HISTORY, INK, REFERENCE, SIMULATED, axis, base, fixed, percent } from "./charts.jsx";
import { TradingView } from "./TradingView.jsx";

function ActivityChart({ daily }) {
  const dense = daily.length > 40;
  const option = {
    ...base(true),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: axis("category", "", {
      data: daily.map((d) => d.date.slice(5)),
      splitLine: { show: false },
      axisLabel: { color: INK, fontSize: 10, interval: daily.length > 12 ? "auto" : 0 },
    }),
    yAxis: axis("value", "帖子数", { minInterval: 1 }),
    series: [
      { name: "原创", key: "original", color: SIMULATED, radius: [0, 0, 0, 0] },
      { name: "转发", key: "reposts", color: HISTORY, radius: [4, 4, 0, 0] },
    ].map((s) => ({
      name: s.name,
      type: "bar",
      stack: "posts",
      barMaxWidth: 28,
      data: daily.map((d) => d[s.key]),
      // 天数多时柱子只有 1–2px，白色描边会盖住填充。
      itemStyle: { color: s.color, borderRadius: dense ? 0 : s.radius, borderColor: "#fff", borderWidth: dense ? 0 : 1 },
    })),
  };
  return <Chart option={option} label={`每日原创帖与转发数，共 ${daily.length} 天`} />;
}

function DepthChart({ distribution }) {
  const option = {
    ...base(false),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: axis("category", "转发链深度（层）", { data: distribution.map((d) => d.depth), splitLine: { show: false } }),
    yAxis: axis("value", "转发链数量", { minInterval: 1 }),
    series: [
      {
        type: "bar",
        barMaxWidth: 28,
        data: distribution.map((d) => d.count),
        itemStyle: { color: HISTORY, borderRadius: [4, 4, 0, 0] },
      },
    ],
  };
  return <Chart option={option} label="转发链深度分布" />;
}

const NODE_GROUPS = [
  { name: "意见领袖", itemStyle: { color: SIMULATED } },
  { name: "其他", itemStyle: { color: REFERENCE } },
];

function NetworkChart({ network, leaderIds, openAgent }) {
  const largest = Math.max(...network.nodes.map((node) => node.pagerank || 0), 1e-9);
  const option = {
    animation: false,
    legend: { top: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: INK, fontSize: 11 }, data: NODE_GROUPS.map((g) => g.name) },
    tooltip: {
      formatter: (item) =>
        item.dataType === "node"
          ? `Agent ${item.data.id}<br/>连接数 ${item.data.degree}<br/>PageRank ${fixed(item.data.pagerank, 4)}`
          : `权重 ${fixed(item.data.value)}`,
    },
    series: [
      {
        type: "graph",
        // 节点位置由后端按全部日期统一计算，拖动时间轴时只有连边变化。
        layout: "none",
        categories: NODE_GROUPS,
        // 只允许平移：滚轮要留给页面滚动。
        roam: "move",
        // 节点大小不随视图缩放，大小只表示 PageRank。
        nodeScaleRatio: 0,
        // 只限定上下边距：四边都限定会把布局拉伸成椭圆。
        top: 32,
        bottom: 12,
        cursor: "pointer",
        lineStyle: { color: "#c4c6cf", opacity: 0.5, width: 1 },
        emphasis: { focus: "adjacency", lineStyle: { width: 2, opacity: 0.9 } },
        data: network.nodes.map((node) => ({
          id: node.id,
          name: node.id,
          degree: node.degree,
          pagerank: node.pagerank,
          x: (node.position?.[0] ?? 0) * 1000,
          y: (node.position?.[1] ?? 0) * 1000,
          category: leaderIds.has(node.id) ? 0 : 1,
          // PageRank 差距通常不大，平方后节点大小才看得出区别。
          symbolSize: 7 + 17 * ((node.pagerank || 0) / largest) ** 2,
          itemStyle: { borderColor: "#fff", borderWidth: 1 },
        })),
        edges: network.edges.map(([source, target, value]) => ({ source, target, value })),
      },
    ],
  };
  return (
    <Chart
      merge
      option={option}
      height={460}
      onClick={(params) => params.dataType === "node" && openAgent(params.data.id)}
      label={`${network.date} 的 Agent 关系网络，${network.node_count} 个节点，${network.edge_count} 条边`}
    />
  );
}

// 拖动时立即更新日期，停顿后才请求该日的网络；读过的日期直接取缓存。
function NetworkPanel({ runId, initial, reason, leaders, openAgent }) {
  const dates = initial?.dates || [];
  const [index, setIndex] = useState(dates.length - 1);
  const [network, setNetwork] = useState(initial);
  const cache = useRef(new Map(initial ? [[initial.date, initial]] : []));
  const date = dates[index];
  useEffect(() => {
    if (!date) return undefined;
    const known = cache.current.get(date);
    if (known) {
      setNetwork(known);
      return undefined;
    }
    let alive = true;
    const timer = setTimeout(async () => {
      try {
        const next = await fetchJson(`/api/runs/${encodeURIComponent(runId)}/analysis/network?date=${encodeURIComponent(date)}`);
        cache.current.set(date, next);
        if (alive) setNetwork(next);
      } catch {
        /* 保留当前显示的网络 */
      }
    }, 60);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [date, runId]);
  if (!network) return <Panel title="Agent 关系网络"><Empty>{reason}</Empty></Panel>;
  const leaderIds = new Set(leaders.slice(0, 10).map((leader) => leader.agent_id));
  return (
    <Panel
      title="Agent 关系网络"
      actions={
        <Text size="2" color="gray" className="tabular">
          {network.truncated ? `${network.nodes.length} / ` : ""}
          {network.node_count} 节点 · {network.edge_count} 边
        </Text>
      }
    >
      <NetworkChart network={network} leaderIds={leaderIds} openAgent={openAgent} />
      {dates.length > 1 && (
        <Flex align="center" gap="4" mt="3">
          <Slider
            aria-label="关系网络日期"
            min={0}
            max={dates.length - 1}
            step={1}
            value={[index]}
            onValueChange={([value]) => setIndex(value)}
          />
          <Text size="2" weight="medium" className="tabular network-date">
            {date}
          </Text>
        </Flex>
      )}
    </Panel>
  );
}

function PropagationView({ runId, refresh, openAgent }) {
  const state = useData(`/api/runs/${encodeURIComponent(runId)}/analysis/propagation`, refresh);
  const d = state.data;
  if (!d) return <Panel title="信息传播"><Empty state={state} /></Panel>;
  if (!d.available) return <Panel title="信息传播"><Empty>{d.reason}</Empty></Panel>;
  const { sample } = d;

  const leaderColumns = [
    ["Agent", (row) => <AgentLink id={row.agent_id} onOpen={openAgent} />],
    ["原创帖", (row) => row.original_posts, "end"],
    ["被直接转发", (row) => row.reposted, "end"],
    ["传播规模", (row) => row.reach, "end"],
    ["赞 / 踩", (row) => `${row.likes} / ${row.unlikes}`, "end"],
    ["连接数", (row) => row.degree ?? "—", "end"],
    ["PageRank", (row) => fixed(row.pagerank, 4), "end"],
  ];
  const cascadeColumns = [
    ["发布日期", (row) => row.date],
    ["作者", (row) => <AgentLink id={row.agent_id} onOpen={openAgent} />],
    ["规模", (row) => row.size, "end"],
    ["深度", (row) => row.depth, "end"],
    ["原帖", (row) => <span className="analysis-excerpt">{row.excerpt}</span>],
  ];

  return (
    <Flex direction="column" gap="4">
      <Grid columns={{ initial: "2", md: "4" }} gap="3">
        <Stat label="原创帖" value={sample.original_posts} />
        <Stat label="转发" value={sample.reposts} />
        <Stat label="转发链" value={sample.cascades} />
        <Stat label="最深转发链" value={`${sample.max_depth} 层`} />
      </Grid>
      <NetworkPanel
        key={d.network?.date || "none"}
        runId={runId}
        initial={d.network}
        reason={d.network_reason}
        leaders={d.leaders}
        openAgent={openAgent}
      />
      <Grid columns={{ initial: "1", md: "2" }} gap="4">
        <Panel title="每日传播量">
          <ActivityChart daily={d.daily} />
        </Panel>
        <Panel title="转发链深度">
          {d.depth_distribution.length ? <DepthChart distribution={d.depth_distribution} /> : <Empty>没有帖子被转发。</Empty>}
        </Panel>
      </Grid>
      <Panel title="意见领袖">
        <DataTable columns={leaderColumns} rows={d.leaders} rowKey={(row) => row.agent_id} />
      </Panel>
      <Panel title="规模最大的转发链">
        {d.cascades.length ? (
          <DataTable columns={cascadeColumns} rows={d.cascades} rowKey={(row) => row.post_id} />
        ) : (
          <Empty>没有帖子被转发。</Empty>
        )}
      </Panel>
    </Flex>
  );
}

const WINDOWS = [
  ["simulated", "模拟结果", SIMULATED],
  ["history", "初始化历史", HISTORY],
];

// 样本不足时说明模拟结果缺什么；历史窗口只是参考，缺了不必解释。
function FactPanel({ title, data, field, children }) {
  const simulated = data.simulated[field];
  const anyAvailable = WINDOWS.some(([id]) => data[id][field].available);
  return (
    <Panel title={title}>
      {anyAvailable ? children : <Empty>{simulated.reason}</Empty>}
      {anyAvailable && !simulated.available && (
        <Text as="p" size="1" color="gray" mt="2">
          {simulated.reason}
        </Text>
      )}
    </Panel>
  );
}

const availableWindows = (data, field) => WINDOWS.filter(([id]) => data[id][field].available);

function DistributionChart({ data }) {
  const shown = availableWindows(data, "distribution");
  const series = shown.flatMap(([id, name, color]) => [
    {
      name,
      type: "line",
      showSymbol: false,
      lineStyle: { color, width: 2 },
      itemStyle: { color },
      data: data[id].distribution.curve.map(([x, density]) => [x, density]),
    },
  ]);
  // 正态参考线取第一个可用窗口：模拟结果优先。
  const reference = data[shown[0][0]].distribution.curve;
  series.push({
    name: `同方差正态（${shown[0][1]}）`,
    type: "line",
    showSymbol: false,
    lineStyle: { color: REFERENCE, width: 2, type: "dashed" },
    itemStyle: { color: REFERENCE },
    data: reference.map(([x, , normal]) => [x, normal]),
  });
  const option = {
    ...base(true),
    tooltip: { trigger: "axis" },
    xAxis: axis("value", "日对数收益率", { scale: true, splitLine: { show: false } }),
    yAxis: axis("value", "概率密度"),
    series,
  };
  return <Chart option={option} label="日对数收益率密度与正态分布对比" />;
}

function ScatterFact({ data, field, xName, yName, label }) {
  const shown = availableWindows(data, field);
  const option = {
    ...base(true),
    tooltip: {
      trigger: "item",
      formatter: (item) => `${item.seriesName}<br/>${xName} ${fixed(item.value[0], 4)}<br/>${yName} ${fixed(item.value[1], 4)}`,
    },
    xAxis: axis("value", xName, { scale: true }),
    yAxis: axis("value", yName, { scale: true }),
    series: shown.map(([id, name, color]) => ({
      name,
      type: "scatter",
      symbolSize: 8,
      large: data[id][field].points.length > 2000,
      itemStyle: { color, opacity: 0.45 },
      data: data[id][field].points,
    })),
  };
  return <Chart option={option} label={label} />;
}

function ClusteringChart({ data }) {
  const shown = availableWindows(data, "clustering");
  const series = shown.flatMap(([id, name, color]) => {
    const rows = data[id].clustering.series;
    return [
      {
        name: `${name}实际波动`,
        type: "line",
        showSymbol: false,
        lineStyle: { color, width: 1.5, opacity: 0.4 },
        itemStyle: { color, opacity: 0.4 },
        data: rows.map((row, index) => [index + 1, row.actual]),
      },
      {
        name: `${name}期望波动`,
        type: "line",
        showSymbol: false,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        data: rows.map((row, index) => [index + 1, row.expected]),
      },
    ];
  });
  const option = {
    ...base(true),
    // 两个窗口共四个系列，窄面板里图例会折成两行。
    grid: { ...base(true).grid, top: 56 },
    tooltip: { trigger: "axis" },
    xAxis: axis("value", "窗口内第几个交易日", { minInterval: 1, splitLine: { show: false } }),
    yAxis: axis("value", "相对波动（1 = 窗口均值）"),
    series,
  };
  return <Chart option={option} label="实际波动与 GARCH 期望波动" />;
}

const FACT_ROWS = [
  ["交易日", (w) => w.days],
  ["观测数（资产 × 日）", (w) => w.observations],
  ["日收益标准差", (w) => percent(w.distribution.std)],
  ["超额峰度", (w) => fixed(w.distribution.excess_kurtosis)],
  ["偏度", (w) => fixed(w.distribution.skewness)],
  ["3σ 外占比（正态约 0.27%）", (w) => percent(w.distribution.tail_share)],
  ["收益与次日绝对收益相关", (w) => fixed(w.leverage.return_vs_next_abs_corr)],
  ["成交量变化与收益相关", (w) => fixed(w.volume_return.volume_return_corr)],
  ["成交量变化与绝对收益相关", (w) => fixed(w.volume_return.volume_abs_return_corr)],
  ["因零成交量跳过的观测", (w) => w.volume_return.zero_volume_skipped],
  ["GARCH α₁", (w) => fixed(w.clustering.alpha)],
  ["GARCH β₁", (w) => fixed(w.clustering.beta)],
  ["绝对收益滞后 1 日自相关", (w) => fixed(w.clustering.acf_abs?.[0]?.value)],
];

function StylizedView({ runId, refresh }) {
  const state = useData(`/api/runs/${encodeURIComponent(runId)}/analysis/stylized-facts`, refresh);
  const d = state.data;
  if (!d) return <Panel title="市场典型事实"><Empty state={state} /></Panel>;
  if (!d.available) return <Panel title="市场典型事实"><Empty>{d.reason}</Empty></Panel>;
  return (
    <Flex direction="column" gap="4">
      <Grid columns={{ initial: "1", md: "2" }} gap="4">
        <FactPanel title="收益率厚尾" field="distribution" data={d}>
          {availableWindows(d, "distribution").length > 0 && <DistributionChart data={d} />}
        </FactPanel>
        <FactPanel title="杠杆效应" field="leverage" data={d}>
          {availableWindows(d, "leverage").length > 0 && (
            <ScatterFact data={d} field="leverage" xName="当日负收益" yName="前一日负收益" label="当日负收益与前一日负收益散点图" />
          )}
        </FactPanel>
        <FactPanel title="量价关系" field="volume_return" data={d}>
          {availableWindows(d, "volume_return").length > 0 && (
            <ScatterFact data={d} field="volume_return" xName="成交量变化" yName="日对数收益率" label="成交量变化与日收益率散点图" />
          )}
        </FactPanel>
        <FactPanel title="波动聚集" field="clustering" data={d}>
          {availableWindows(d, "clustering").length > 0 && <ClusteringChart data={d} />}
        </FactPanel>
      </Grid>
      <Panel title="统计量">
        <DataTable
          columns={[
            ["指标", (row) => row[0]],
            ...WINDOWS.map(([id, name]) => [name, (row) => row[1](d[id]) ?? "—", "end"]),
          ]}
          rows={FACT_ROWS}
          rowKey={(row) => row[0]}
        />
      </Panel>
    </Flex>
  );
}

function PolarizationView({ runId, refresh }) {
  const state = useData(`/api/runs/${encodeURIComponent(runId)}/analysis/polarization`, refresh);
  return (
    <Panel title="信息极化">
      <Empty state={state}>{state.data?.reason}</Empty>
    </Panel>
  );
}

const VIEWS = [
  ["market", "行情"],
  ["trading", "交易行为"],
  ["propagation", "信息传播"],
  ["stylized", "典型事实"],
  ["polarization", "信息极化"],
  ["agents", "Agents"],
];

const exportUrl = (runId, kind) => `/api/runs/${encodeURIComponent(runId)}/results/${kind}/export?format=csv`;

export function AnalysisTab({ run, snapshot, live, now, view, onView, refresh, openAgent }) {
  const current = VIEWS.some(([id]) => id === view) ? view : VIEWS[0][0];
  const runId = run.run_id;
  return (
    <Flex direction="column" gap="4">
      <Flex align="center" justify="between" gap="3" wrap="wrap">
        <div className="subnav">
          <SegmentedControl.Root value={current} onValueChange={onView}>
            {VIEWS.map(([id, label]) => (
              <SegmentedControl.Item key={id} value={id}>
                {label}
              </SegmentedControl.Item>
            ))}
          </SegmentedControl.Root>
        </div>
        {current === "trading" && (
          <Flex gap="2">
            {[["trades", "成交"], ["orders", "委托"]].map(([kind, label]) => (
              <Button key={kind} asChild size="1" variant="soft" color="gray">
                <a href={exportUrl(runId, kind)}>
                  {icon(DownloadIcon, 12)} {label}
                </a>
              </Button>
            ))}
          </Flex>
        )}
      </Flex>
      {current === "market" && <MarketChart run={runId} refresh={refresh} startDate={run.config.start_date} />}
      {current === "trading" && <TradingView runId={runId} refresh={refresh} openAgent={openAgent} />}
      {current === "propagation" && <PropagationView runId={runId} refresh={refresh} openAgent={openAgent} />}
      {current === "stylized" && <StylizedView runId={runId} refresh={refresh} />}
      {current === "polarization" && <PolarizationView runId={runId} refresh={refresh} />}
      {current === "agents" && <AgentDistribution runId={runId} refresh={refresh} />}
      {current === "agents" && (
        <AgentExplorer
          runId={runId}
          runtime={snapshot}
          live={live}
          now={now}
          timeout={run.config?.agent_timeout ?? 300}
          onSelect={openAgent}
          refresh={refresh}
        />
      )}
    </Flex>
  );
}
