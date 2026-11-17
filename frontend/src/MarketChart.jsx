import React, { useEffect, useRef, useState } from "react";
import { Flex, Select, SegmentedControl, Table, Text } from "@radix-ui/themes";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { n, useData } from "./lib.js";
import { Empty, Panel } from "./ui.jsx";

// 按需注册 ECharts 模块，避免引入未使用的图表类型和渲染器。
echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

// ECharts 实例的创建、ResizeObserver 订阅和销毁都限定在同一个 effect 生命周期中。
function PriceChart({ prices, startDate }) {
  const el = useRef(null);
  useEffect(() => {
    const chart = echarts.init(el.current);
    const area = {
      color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: "#12a59433" },
        { offset: 1, color: "#12a59400" },
      ]),
    };
    // 有起始日时，初始化历史和模拟结果分成两段，并在起始日前一点相接。
    const first = startDate ? prices.findIndex((p) => p.date >= startDate) : 0;
    const split = startDate && first > 0;
    const close = (keep) => prices.map((p, index) => (keep(index) ? p.close_price : null));
    const line = (name, color, data, extra = {}) => ({
      name,
      type: "line",
      data,
      showSymbol: false,
      connectNulls: false,
      itemStyle: { color },
      lineStyle: { color, width: 2 },
      ...extra,
    });
    chart.setOption({
      animation: false,
      grid: { left: 57, right: 20, top: split ? 34 : 18, bottom: 30 },
      legend: split ? { top: 0, itemWidth: 14, itemHeight: 8, textStyle: { color: "#60646c", fontSize: 11 } } : undefined,
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: prices.map((p) => p.date),
        boundaryGap: false,
        axisLabel: { color: "#60646c", fontSize: 10 },
        axisLine: { lineStyle: { color: "#e0e1e6" } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: "#60646c", fontSize: 10 },
        splitLine: { lineStyle: { color: "#f0f0f3", type: "dashed" } },
      },
      series: split
        ? [
            line("初始化历史", "#3e63dd", close((index) => index < first)),
            line("模拟结果", "#12a594", close((index) => index >= first - 1), { areaStyle: area }),
          ]
        : [line("收盘价", "#12a594", close(() => true), { areaStyle: area })],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [prices, startDate]);
  return (
    <div
      ref={el}
      className="price-chart"
      role="img"
      aria-label={`收盘价曲线，共 ${prices.length} 个观测点`}
    />
  );
}

// Radix Select 不接受空字符串，内置历史文件用占位值表示。
const LEGACY = "__legacy__";
const SERVICE = "__service__";

// 行情组件支持在图表和可读取的表格间切换，二者共用同一份 API 数据。
// selectSource 时可在内置历史文件与市场数据服务（最新数据版本）之间切换；服务可用时默认读取服务。
export function MarketChart({ run = "template", refresh = 0, selectSource = false, startDate = null }) {
  const [asset, setAsset] = useState("");
  const [view, setView] = useState("chart");
  const [picked, setPicked] = useState(null);
  const health = useData(selectSource ? "/mds/v1/health" : null, refresh);
  const source = picked ?? (health.data ? SERVICE : LEGACY);
  // 服务状态读取前不请求行情，避免先闪现内置历史再切换。
  const waiting = selectSource && !health.data && !health.error;
  const url = waiting
    ? null
    : source === SERVICE
      ? `/mds/v1/market/series?asset=${asset}`
      : `/api/runs/${encodeURIComponent(run)}/market?asset=${asset}`;
  const state = useData(url, refresh);
  if (!state.data)
    return (
      <Panel title="行情">
        <Empty state={state} />
      </Panel>
    );
  const d = state.data,
    last = d.prices.at(-1);
  return (
    <Panel
      title="行情"
      actions={
        <>
          {selectSource && health.data && (
            <Select.Root value={source} onValueChange={setPicked}>
              <Select.Trigger aria-label="选择行情来源" />
              <Select.Content>
                <Select.Item value={SERVICE}>市场数据服务 · v{health.data.data_version}</Select.Item>
                <Select.Item value={LEGACY}>内置历史文件</Select.Item>
              </Select.Content>
            </Select.Root>
          )}
          <Select.Root value={d.selected} onValueChange={setAsset}>
            <Select.Trigger aria-label="选择资产" />
            <Select.Content>
              {d.assets.map((a) => (
                <Select.Item key={a.stock_id} value={a.stock_id}>
                  {a.name} · {a.stock_id}
                </Select.Item>
              ))}
            </Select.Content>
          </Select.Root>
          <SegmentedControl.Root value={view} onValueChange={setView} size="1">
            <SegmentedControl.Item value="chart">曲线</SegmentedControl.Item>
            <SegmentedControl.Item value="table">数据</SegmentedControl.Item>
          </SegmentedControl.Root>
        </>
      }
    >
      {state.error && (
        <Text size="1" color="amber">
          {state.error}，保留上次数据
        </Text>
      )}
      <Flex align="baseline" gap="2" mb="2">
        <Text size="6" weight="bold" className="tabular">
          {n(last?.close_price)}
        </Text>
        <Text size="2" color="gray" className="tabular">
          {last?.date || "—"}
        </Text>
      </Flex>
      {view === "table" ? (
        <div className="scroll-table">
          <Table.Root size="1">
            <Table.Header>
              <Table.Row>
                <Table.ColumnHeaderCell>日期</Table.ColumnHeaderCell>
                <Table.ColumnHeaderCell justify="end">收盘价</Table.ColumnHeaderCell>
                <Table.ColumnHeaderCell justify="end">变动 (%)</Table.ColumnHeaderCell>
                <Table.ColumnHeaderCell justify="end">成交量</Table.ColumnHeaderCell>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {[...d.prices].reverse().map((p) => (
                <Table.Row key={p.date}>
                  <Table.Cell>{p.date}</Table.Cell>
                  <Table.Cell justify="end" className="tabular">{n(p.close_price)}</Table.Cell>
                  <Table.Cell justify="end" className="tabular">{n(p.pct_chg)}</Table.Cell>
                  <Table.Cell justify="end" className="tabular">{n(p.vol)}</Table.Cell>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        </div>
      ) : (
        <PriceChart prices={d.prices} startDate={startDate} />
      )}
    </Panel>
  );
}
