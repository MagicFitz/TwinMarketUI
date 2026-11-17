import React, { useState } from "react";
import { Box, Flex, Grid, SegmentedControl, Text } from "@radix-ui/themes";
import { useData } from "../lib.js";
import { Empty, Panel } from "../ui.jsx";
import { Chart, HISTORY, INK, REFERENCE, money } from "../experiments/charts.jsx";

/*
 * Agent 群体的分类统计，取每个 Agent 的最早快照（初始状态）。
 * 单选分类用饼图；一人多选的持仓和关注行业占比之和超过 100%，不构成整体，保留条形图。
 */

// 已通过 dataviz 校验（adjacent）；对比度偏低的色块由图例旁的数值补足。
const CATEGORICAL = ["#3e63dd", "#e0702a", "#12a594", "#d99a00", "#d6609f", "#2f8f4e", "#7a5cd6", "#d9484d"];
// 有序等级和数值区间用同一色相由浅到深。
const RAMP = ["#8da4ef", "#3e63dd", "#2c4aa8", "#1f2d5c"];
const LEVELS = [
  { key: "低", color: "#abbdf9" },
  { key: "中", color: HISTORY },
  { key: "高", color: "#1f2d5c" },
];
// 颜色跟随实体：同一地区或类型在不同运行里颜色不变，名单外的归入中性色的“其他”。
const LOCATIONS = ["上海", "北京", "香港", "广东", "湖南", "山东", "浙江"];
const USER_TYPES = ["普通股民", "小博主", "大V"];
const GENDERS = ["男", "女"];
const STRATEGIES = ["技术面", "基本面"];

const ROW_HEIGHT = 28;
const share = (value, total) => (total ? (100 * value) / total : 0);

function byEntity(items, order) {
  const known = items
    .filter((item) => order.includes(item.name))
    .map((item) => ({ ...item, color: CATEGORICAL[order.indexOf(item.name)] }));
  const rest = items.filter((item) => !order.includes(item.name));
  if (!rest.length) return known;
  const name = rest.length === 1 ? rest[0].name : `其他（${rest.map((item) => item.name).join("、")}）`;
  return [...known, { name, value: rest.reduce((sum, item) => sum + item.value, 0), color: REFERENCE }];
}

function byScale(items, name = (item) => item.name) {
  const step = (RAMP.length - 1) / Math.max(1, items.length - 1);
  return items.map((item, index) => ({ ...item, name: name(item), color: RAMP[Math.round(index * step)] }));
}

function pieOption(items, total, radius, gap) {
  return {
    animation: false,
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: ({ marker, name, value }) => `${marker}${name}<br/>${value} · ${share(value, total).toFixed(1)}%`,
    },
    series: [
      {
        type: "pie",
        radius,
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderColor: "#ffffff", borderWidth: gap },
        data: items
          .filter((item) => item.value > 0)
          .map(({ name, value, color }) => ({ name, value, itemStyle: { color } })),
      },
    ],
  };
}

const Swatch = ({ color }) => <i className="pie-swatch" style={{ background: color }} />;

function Pie({ title, items, total }) {
  const summary = items.map((item) => `${item.name} ${item.value}`).join("，");
  return (
    <Box minWidth="0">
      <Text as="div" size="2" weight="medium" mb="2">
        {title}
      </Text>
      <Flex align="center" gap="4" wrap="wrap">
        <Box flexShrink="0" width="148px">
          <Chart option={pieOption(items, total, "94%", 2)} label={`${title}：${summary}`} height={148} />
        </Box>
        <Box flexGrow="1" flexBasis="150px" minWidth="0">
          {items.map((item) => (
            <div key={item.name} className="pie-row">
              <Swatch color={item.color} />
              <Text size="1" className="pie-name">{item.name}</Text>
              <Text size="1" color="gray" className="tabular">
                {item.value} · {share(item.value, total).toFixed(1)}%
              </Text>
            </div>
          ))}
        </Box>
      </Flex>
    </Box>
  );
}

// total 是全部成员数：持仓和关注行业一人多选，百分比之和可以超过 100%。
function BarList({ title, items, total }) {
  const describe = (value) => `${value} · ${share(value, total).toFixed(1)}%`;
  const option = {
    animation: false,
    grid: { left: 4, right: 76, top: 2, bottom: 2, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: true,
      valueFormatter: describe,
    },
    xAxis: { type: "value", show: false, min: 0, max: Math.max(1, ...items.map((item) => item.value)) },
    yAxis: {
      type: "category",
      inverse: true,
      data: items.map((item) => item.name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: INK, fontSize: 11 },
    },
    series: [
      {
        name: title,
        type: "bar",
        data: items.map((item) => item.value),
        barWidth: 12,
        itemStyle: { color: HISTORY, borderRadius: [0, 4, 4, 0] },
        label: {
          show: true,
          position: "right",
          color: INK,
          fontSize: 11,
          formatter: ({ value }) => describe(value),
        },
      },
    ],
  };
  const summary = items.map((item) => `${item.name} ${item.value}`).join("，");
  return (
    <Box minWidth="0">
      <Text as="div" size="2" weight="medium" mb="2">
        {title}
      </Text>
      <Chart option={option} label={`${title}：${summary}`} height={items.length * ROW_HEIGHT + 4} />
    </Box>
  );
}

function BehaviorPies({ items }) {
  return (
    <Box minWidth="0">
      <Flex align="center" justify="between" gap="3" mb="2">
        <Text size="2" weight="medium">
          行为特征
        </Text>
        <Flex gap="3">
          {LEVELS.map(({ key, color }) => (
            <Flex key={key} align="center" gap="1">
              <Swatch color={color} />
              <Text size="1" color="gray">{key}</Text>
            </Flex>
          ))}
        </Flex>
      </Flex>
      <Grid columns="3" gap="3">
        {items.map((item) => {
          const total = LEVELS.reduce((sum, { key }) => sum + item[key], 0);
          const parts = LEVELS.map(({ key, color }) => ({ name: key, value: item[key], color }));
          const summary = parts.map((part) => `${part.name} ${part.value}`).join("，");
          return (
            <Box key={item.name} className="behavior-pie">
              <Chart option={pieOption(parts, total, "92%", 1)} label={`${item.name}：${summary}`} height={84} />
              <Text as="div" size="1">{item.name}</Text>
              <Text as="div" size="1" color="gray" className="tabular">
                {parts.map((part) => Math.round(share(part.value, total))).join(" · ")}%
              </Text>
            </Box>
          );
        })}
      </Grid>
    </Box>
  );
}

const GROUPS = {
  people: {
    label: "地域与人群",
    render: (d) => (
      <>
        <Pie title="地域分布" items={byEntity(d.location, LOCATIONS)} total={d.total} />
        <Pie title="投资者类型" items={byEntity(d.user_type, USER_TYPES)} total={d.total} />
        <Pie title="性别" items={byEntity(d.gender, GENDERS)} total={d.total} />
      </>
    ),
  },
  holdings: {
    label: "初始持仓",
    render: (d) => (
      <>
        <Pie title="初始资金" items={byScale(d.initial_cash, (item) => money(item.name))} total={d.total} />
        <Pie title="持仓市值占总资产" items={byScale(d.exposure)} total={d.total} />
        <BarList title="持仓指数" items={d.holdings} total={d.total} />
      </>
    ),
  },
  style: {
    label: "投资风格",
    render: (d) => (
      <>
        <Pie title="投资策略" items={byEntity(d.strategy, STRATEGIES)} total={d.total} />
        <BehaviorPies items={d.behaviors} />
        <BarList title="关注行业" items={d.industries} total={d.total} />
      </>
    ),
  },
};

export function AgentDistribution({ runId, title = "群体分布", refresh = 0 }) {
  const [group, setGroup] = useState("people");
  const state = useData(`/api/runs/${encodeURIComponent(runId)}/agents/distribution`, refresh);
  return (
    <Panel
      title={title}
      actions={
        <SegmentedControl.Root value={group} onValueChange={setGroup} size="1">
          {Object.entries(GROUPS).map(([id, { label }]) => (
            <SegmentedControl.Item key={id} value={id}>
              {label}
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
      }
    >
      {state.data?.total ? (
        <Grid columns={{ initial: "1", md: "3" }} gap="5" align="start">
          {GROUPS[group].render(state.data)}
        </Grid>
      ) : (
        <Empty state={state}>{state.data ? "没有成员" : undefined}</Empty>
      )}
    </Panel>
  );
}
