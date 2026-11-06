import React, { useEffect, useRef } from "react";
import { Button, Table } from "@radix-ui/themes";
import * as echarts from "echarts/core";
import { BarChart, GraphChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, GraphChart, LineChart, ScatterChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

// 颜色由实体决定：模拟结果、初始化历史和参考线在所有图中保持一致。
// 这组颜色已通过 dataviz 校验（all-pairs）；对比度不足的部分由图例和统计表补足。
export const SIMULATED = "#12a594";
export const HISTORY = "#3e63dd";
export const REFERENCE = "#8b8d98";
export const INK = "#60646c";
const GRID = "#f0f0f3";
const LINE = "#e0e1e6";

export const axis = (type, name, extra = {}) => ({
  type,
  name,
  nameLocation: "middle",
  nameGap: type === "value" ? 44 : 26,
  nameTextStyle: { color: INK, fontSize: 11 },
  axisLabel: { color: INK, fontSize: 10 },
  axisLine: { lineStyle: { color: LINE } },
  axisTick: { show: false },
  splitLine: { lineStyle: { color: GRID, type: "dashed" } },
  ...extra,
});

export const base = (hasLegend) => ({
  animation: false,
  grid: { left: 58, right: 18, top: hasLegend ? 34 : 14, bottom: 44 },
  legend: hasLegend ? { top: 0, itemWidth: 14, itemHeight: 8, textStyle: { color: INK, fontSize: 11 } } : undefined,
});

export const percent = (value) => (value == null ? "—" : `${(value * 100).toFixed(2)}%`);
export const fixed = (value, digits = 3) => (value == null ? "—" : Number(value).toFixed(digits));
export const money = (value) => {
  if (value == null) return "—";
  const size = Math.abs(value);
  if (size >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`;
  if (size >= 1e4) return `${(value / 1e4).toFixed(0)} 万`;
  return Number(value).toFixed(0);
};

// 同一内容的重复渲染不重绘：工作台轮询会让父组件定时刷新。
// merge 保留缩放和平移，用于随时间轴更新的关系网络。
export function Chart({ option, label, height = 260, onClick, merge = false }) {
  const el = useRef(null);
  const chart = useRef(null);
  const click = useRef(onClick);
  click.current = onClick;
  useEffect(() => {
    const instance = echarts.init(el.current);
    chart.current = instance;
    instance.on("click", (params) => click.current?.(params));
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(el.current);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);
  const signature = JSON.stringify(option);
  useEffect(() => {
    chart.current.setOption(option, !merge);
  }, [signature]);
  return <div ref={el} className="analysis-chart" style={{ height }} role="img" aria-label={label} />;
}

export function AgentLink({ id, onOpen }) {
  return (
    <Button variant="ghost" size="1" onClick={() => onOpen(id)}>
      {id}
    </Button>
  );
}

export function DataTable({ columns, rows, rowKey }) {
  return (
    <div className="scroll-table">
      <Table.Root size="1">
        <Table.Header>
          <Table.Row>
            {columns.map(([label, , align]) => (
              <Table.ColumnHeaderCell key={label} justify={align}>
                {label}
              </Table.ColumnHeaderCell>
            ))}
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {rows.map((row) => (
            <Table.Row key={rowKey(row)}>
              {columns.map(([label, read, align]) => (
                <Table.Cell key={label} justify={align} className={align === "end" ? "tabular" : undefined}>
                  {read(row)}
                </Table.Cell>
              ))}
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </div>
  );
}
