import React from "react";
import { Flex, Grid, Text } from "@radix-ui/themes";
import { n, useData } from "../lib.js";
import { Empty, Panel, Stat } from "../ui.jsx";
import { AgentLink, Chart, DataTable, HISTORY, INK, SIMULATED, axis, base, fixed, money, percent } from "./charts.jsx";

const SIDES = [
  ["买入", "buy", SIMULATED],
  ["卖出", "sell", HISTORY],
];

const correlation = (value) => (value == null ? null : <Text size="2" color="gray" className="tabular">r = {fixed(value, 2)}</Text>);

function DailyChart({ daily }) {
  const option = {
    ...base(true),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: money },
    xAxis: axis("category", "", {
      data: daily.map((d) => d.date.slice(5)),
      splitLine: { show: false },
      axisLabel: { color: INK, fontSize: 10, interval: daily.length > 12 ? "auto" : 0 },
    }),
    yAxis: axis("value", "成交额", { axisLabel: { color: INK, fontSize: 10, formatter: money } }),
    series: SIDES.map(([name, key, color]) => ({
      name,
      type: "bar",
      stack: "amount",
      barMaxWidth: 28,
      data: daily.map((d) => d[key]),
      itemStyle: { color },
    })),
  };
  return <Chart option={option} label={`每日买入额与卖出额，共 ${daily.length} 天`} />;
}

function AssetChart({ assets }) {
  const rows = [...assets].reverse();
  const option = {
    ...base(true),
    grid: { left: 118, right: 18, top: 34, bottom: 30 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: money },
    xAxis: axis("value", "", { axisLabel: { color: INK, fontSize: 10, formatter: money } }),
    yAxis: axis("category", "", { data: rows.map((d) => d.name), splitLine: { show: false } }),
    series: SIDES.map(([name, key, color]) => ({
      name,
      type: "bar",
      stack: "amount",
      barMaxWidth: 16,
      data: rows.map((d) => d[key]),
      itemStyle: { color },
    })),
  };
  return <Chart option={option} height={320} label="各资产的买入额与卖出额" />;
}

function DispositionChart({ disposition }) {
  const option = {
    ...base(true),
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: percent },
    xAxis: axis("category", "处置效应画像", { data: disposition.groups.map((g) => g.category), splitLine: { show: false } }),
    yAxis: axis("value", "实现比例", { axisLabel: { color: INK, fontSize: 10, formatter: (v) => `${(v * 100).toFixed(0)}%` } }),
    series: [
      ["PGR 盈利实现", "pgr", SIMULATED],
      ["PLR 亏损实现", "plr", HISTORY],
    ].map(([name, key, color]) => ({
      name,
      type: "bar",
      barMaxWidth: 28,
      data: disposition.groups.map((g) => g[key]),
      itemStyle: { color, borderRadius: [4, 4, 0, 0] },
    })),
  };
  return <Chart option={option} label="按处置效应画像分组的盈利实现比例和亏损实现比例" />;
}

function ImbalanceChart({ imbalance }) {
  const option = {
    ...base(false),
    tooltip: {
      trigger: "item",
      formatter: (item) => `委托失衡 ${fixed(item.value[0], 3)}<br/>收益率 ${fixed(item.value[1], 4)}`,
    },
    xAxis: axis("value", "委托失衡（买 − 卖）/（买 + 卖）", { min: -1, max: 1 }),
    yAxis: axis("value", "日对数收益率", { scale: true }),
    series: [
      {
        type: "scatter",
        symbolSize: 7,
        large: imbalance.points.length > 2000,
        itemStyle: { color: SIMULATED, opacity: 0.45 },
        data: imbalance.points,
      },
    ],
  };
  return <Chart option={option} label={`委托失衡与当日收益率，共 ${imbalance.points.length} 个资产日`} />;
}

function TurnoverChart({ turnover, openAgent }) {
  const option = {
    ...base(false),
    tooltip: {
      trigger: "item",
      formatter: (item) =>
        `Agent ${item.data.agent_id}<br/>换手 ${fixed(item.data.value[0], 2)} 倍<br/>收益率 ${percent(item.data.value[1])}`,
    },
    xAxis: axis("value", "换手（倍）", { scale: true }),
    yAxis: axis("value", "区间收益率", {
      scale: true,
      axisLabel: { color: INK, fontSize: 10, formatter: (v) => `${(v * 100).toFixed(0)}%` },
    }),
    series: [
      {
        type: "scatter",
        symbolSize: 10,
        cursor: "pointer",
        itemStyle: { color: SIMULATED, opacity: 0.6 },
        data: turnover.points.map((p) => ({ agent_id: p.agent_id, value: [p.turnover, p.return] })),
      },
    ],
  };
  return (
    <Chart
      option={option}
      onClick={(params) => params.data?.agent_id && openAgent(params.data.agent_id)}
      label={`各 Agent 的换手与区间收益率，共 ${turnover.points.length} 个 Agent`}
    />
  );
}

export function TradingView({ runId, refresh, openAgent }) {
  const state = useData(`/api/runs/${encodeURIComponent(runId)}/analysis/trading`, refresh);
  const d = state.data;
  if (!d) return <Panel title="交易行为"><Empty state={state} /></Panel>;
  if (!d.available) return <Panel title="交易行为"><Empty>{d.reason}</Empty></Panel>;
  const { sample, disposition, imbalance, turnover } = d;
  const columns = [
    ["Agent", (row) => <AgentLink id={row.agent_id} onOpen={openAgent} />],
    ["成交笔数", (row) => n(row.trades), "end"],
    ["买入额", (row) => money(row.buy), "end"],
    ["卖出额", (row) => money(row.sell), "end"],
    ["换手（倍）", (row) => fixed(row.turnover, 2), "end"],
    ["收益率", (row) => percent(row.return), "end"],
  ];
  return (
    <Flex direction="column" gap="4">
      <Grid columns={{ initial: "2", md: "5" }} gap="3">
        <Stat label="买入额" value={money(sample.buy)} />
        <Stat label="卖出额" value={money(sample.sell)} />
        <Stat label="成交笔数" value={n(sample.trades)} />
        <Stat label="成交 Agent" value={n(sample.agents)} />
        <Stat label="委托成交率" value={percent(sample.fill_rate)} />
      </Grid>
      <Grid columns={{ initial: "1", md: "2" }} gap="4">
        <Panel title="每日成交额">
          <DailyChart daily={d.daily} />
        </Panel>
        <Panel title="处置效应" actions={disposition.available && <Text size="2" color="gray" className="tabular">PGR − PLR = {fixed(disposition.difference)}</Text>}>
          {disposition.available ? <DispositionChart disposition={disposition} /> : <Empty>{disposition.reason}</Empty>}
        </Panel>
        <Panel title="委托失衡与收益" actions={correlation(imbalance.correlation)}>
          {imbalance.available ? <ImbalanceChart imbalance={imbalance} /> : <Empty>{imbalance.reason}</Empty>}
        </Panel>
        <Panel title="换手与收益" actions={correlation(turnover.correlation)}>
          {turnover.available ? <TurnoverChart turnover={turnover} openAgent={openAgent} /> : <Empty>{turnover.reason}</Empty>}
        </Panel>
      </Grid>
      <Grid columns={{ initial: "1", md: "2" }} gap="4">
        <Panel title="资产成交">
          <AssetChart assets={d.assets} />
        </Panel>
        <Panel title="成交最活跃的 Agent">
          <DataTable columns={columns} rows={d.active} rowKey={(row) => row.agent_id} />
        </Panel>
      </Grid>
    </Flex>
  );
}
