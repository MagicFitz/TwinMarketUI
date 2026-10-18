import React, { useEffect, useRef, useState } from "react";
import { Badge, Flex, Select, SegmentedControl, Table, Text } from "@radix-ui/themes";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { n, useData } from "./lib.js";
import { Empty, Panel } from "./ui.jsx";

// 按需注册 ECharts 模块，避免引入未使用的图表类型和渲染器。
echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

// ECharts 实例的创建、ResizeObserver 订阅和销毁都限定在同一个 effect 生命周期中。
function PriceChart({ prices }) {
  const el = useRef(null);
  useEffect(() => {
    const chart = echarts.init(el.current);
    chart.setOption({
      animation: false,
      grid: { left: 57, right: 20, top: 18, bottom: 30 },
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
      series: [
        {
          type: "line",
          data: prices.map((p) => p.close_price),
          showSymbol: false,
          lineStyle: { color: "#12a594", width: 2 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "#12a59433" },
              { offset: 1, color: "#12a59400" },
            ]),
          },
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [prices]);
  return (
    <div
      ref={el}
      className="price-chart"
      role="img"
      aria-label={`收盘价曲线，共 ${prices.length} 个观测点；可切换数据表读取数值`}
    />
  );
}

// 行情组件支持在图表和可读取的表格间切换，二者共用同一份 API 数据。
export function MarketChart({ run = "template", refresh = 0 }) {
  const [asset, setAsset] = useState("");
  const [view, setView] = useState("chart");
  const state = useData(`/api/runs/${encodeURIComponent(run)}/market?asset=${asset}`, refresh);
  if (!state.data)
    return (
      <Panel title="行情观察">
        <Empty state={state} />
      </Panel>
    );
  const d = state.data,
    last = d.prices.at(-1);
  return (
    <Panel
      title="行情观察"
      hint={`${d.source}，最近 ${d.prices.length} 个观测点。包含初始化历史；非实时交易报价，使用收盘价而非 OHLC。`}
      actions={
        <>
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
        <Text size="1" color="gray">
          收盘价
        </Text>
        <Badge color="gray" variant="soft">
          截至 {last?.date || "—"}
        </Badge>
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
        <PriceChart prices={d.prices} />
      )}
    </Panel>
  );
}
