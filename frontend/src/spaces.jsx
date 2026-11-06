import React, { useState } from "react";
import { Badge, Box, Button, Card, Flex, Grid, Heading, SegmentedControl, Table, Tabs, Text, Tooltip } from "@radix-ui/themes";
import { n, useData } from "./lib.js";
import { Definition, Empty, PageHeader, Panel, Stat } from "./ui.jsx";
import { AgentDialog, AgentExplorer } from "./agents/AgentExplorer.jsx";
import { MarketChart } from "./MarketChart.jsx";
import { MarketWorkbench, TushareConnection } from "./MarketData.jsx";
import { ModelSettings } from "./ModelSettings.jsx";

// 尚未开放的写操作：保持可聚焦，用 Tooltip 说明原因。
function PlannedButton({ children, variant = "soft" }) {
  return (
    <Tooltip content="只读模式，暂未开放。">
      <Button variant={variant} color={variant === "solid" ? undefined : "gray"} disabled aria-label={`${children}（暂未开放）`}>
        {children}
      </Button>
    </Tooltip>
  );
}

export function AgentSpace({ catalog, refresh }) {
  const g = catalog.group;
  const [selected, setSelected] = useState(null);
  return (
    <>
      <PageHeader title="Agent 群体" actions={<PlannedButton variant="solid">创建群体</PlannedButton>} />
      <Panel
        title={g.name}
        actions={
          <>
            <Badge color="gray" variant="soft">本地模板</Badge>
            <PlannedButton>行为金融测试</PlannedButton>
          </>
        }
      >
        <Flex align="center" gap="6" wrap="wrap">
          <Box>
            <Text size="8" weight="bold" className="tabular">
              {n(g.users)}
            </Text>
            <Text size="2" color="gray" ml="2">
              位投资者
            </Text>
          </Box>
          <Flex gap="5" wrap="wrap">
            {g.distributions.map((d) => (
              <Box key={d.name}>
                <Text as="div" size="1" color="gray">
                  {d.name}
                </Text>
                <Text size="3" weight="medium" className="tabular">
                  {n(d.value)}
                </Text>
              </Box>
            ))}
          </Flex>
        </Flex>
      </Panel>
      <Box mt="4">
        <AgentExplorer runId="template" onSelect={setSelected} refresh={refresh} />
        {selected && <AgentDialog runId="template" agentId={selected} onClose={() => setSelected(null)} />}
      </Box>
    </>
  );
}

export function MarketSpace({ catalog, refresh }) {
  const [tab, setTab] = useState("environment");
  const state = useData("/api/runs/template/market", refresh);
  return (
    <>
      <PageHeader title="市场" actions={<PlannedButton variant="solid">创建环境</PlannedButton>} />
      <Tabs.Root value={tab} onValueChange={setTab}>
        <Tabs.List mb="4">
          <Tabs.Trigger value="environment">模拟环境</Tabs.Trigger>
          <Tabs.Trigger value="mechanisms">交易机制</Tabs.Trigger>
          <Tabs.Trigger value="assets">资产组合包</Tabs.Trigger>
          <Tabs.Trigger value="data">数据工作台</Tabs.Trigger>
        </Tabs.List>
      </Tabs.Root>
      {tab === "environment" && (
        <Flex direction="column" gap="4">
          <Panel title="行业指数市场" actions={<Badge color="gray" variant="soft">本地模板</Badge>}>
            <Grid columns={{ initial: "1", sm: "2", md: "3" }} gap="4" align="center">
              <Stat label="可交易资产" value={n(state.data?.assets.length)} />
              <Stat label="历史行情记录" value={n(catalog.market_range.records)} />
              <Definition
                values={[
                  ["机制", "日级最大成交量撮合"],
                  ["数据起始", catalog.market_range.start],
                  ["数据截至", catalog.market_range.end],
                ]}
              />
            </Grid>
          </Panel>
          <MarketChart refresh={refresh} selectSource />
        </Flex>
      )}
      {tab === "mechanisms" && (
        <Panel title="内置撮合机制">
          <Definition
            values={[
              ["订单优先级", "价格优先、时间优先"],
              ["价格形成", "日级最大成交量撮合"],
              ["实现", "trader/matching_engine.py"],
              ["交易日历", "data/trading_days.csv"],
            ]}
          />
        </Panel>
      )}
      {tab === "assets" && (
        <Panel title="行业指数包">
          {state.data ? (
            <Table.Root size="2">
              <Table.Header>
                <Table.Row>
                  <Table.ColumnHeaderCell>代码</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell>名称</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell>行业</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell justify="end">权重</Table.ColumnHeaderCell>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {state.data.assets.map((a) => (
                  <Table.Row key={a.stock_id}>
                    <Table.RowHeaderCell>{a.stock_id}</Table.RowHeaderCell>
                    <Table.Cell>{a.name}</Table.Cell>
                    <Table.Cell>{a.industry}</Table.Cell>
                    <Table.Cell justify="end" className="tabular">
                      {n(a.weight)}
                    </Table.Cell>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          ) : (
            <Empty state={state} />
          )}
        </Panel>
      )}
      {tab === "data" && <MarketWorkbench />}
    </>
  );
}

export function SettingSpace({ compact, setCompact }) {
  return (
    <>
      <PageHeader title="设置" />
      <Flex direction="column" gap="4" className="settings">
        <section id="models">
          <Panel title="模型连接">
            <ModelSettings />
          </Panel>
        </section>
        <section id="sources">
          <Panel title="金融数据源">
            <TushareConnection />
          </Panel>
        </section>
        <section id="preferences">
          <Panel title="界面偏好">
            <Flex align="center" justify="between" gap="4">
              <Text size="2" weight="medium">
                信息密度
              </Text>
              <SegmentedControl.Root value={compact ? "compact" : "comfortable"} onValueChange={(v) => setCompact(v === "compact")}>
                <SegmentedControl.Item value="comfortable">舒适</SegmentedControl.Item>
                <SegmentedControl.Item value="compact">紧凑</SegmentedControl.Item>
              </SegmentedControl.Root>
            </Flex>
          </Panel>
        </section>
      </Flex>
    </>
  );
}
