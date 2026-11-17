import React, { useState } from "react";
import { Badge, Box, Button, Card, Flex, Grid, Heading, SegmentedControl, Table, Tabs, Text, Tooltip } from "@radix-ui/themes";
import { n, useData } from "./lib.js";
import { Definition, Empty, PageHeader, Panel, Stat } from "./ui.jsx";
import { AgentDialog, AgentExplorer } from "./agents/AgentExplorer.jsx";
import { AgentDistribution } from "./agents/AgentDistribution.jsx";
import { MarketChart } from "./MarketChart.jsx";
import { MarketWorkbench, TushareConnection } from "./MarketData.jsx";
import { ModelSettings } from "./ModelSettings.jsx";
import { DEFAULTS } from "./experiments/NewExperiment.jsx";
import { useRouter } from "./router.jsx";

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

// 依据论文附录 D（arXiv:2502.01506）。
const PERSONA_INTRO =
  "LLM 投资者群体以真实交易行为为基础构建：先从雪球 639 名用户的 11,965 条交易记录中估计处置效应、彩票偏好、投资集中度和换手率，再为每个投资者配置性别、地域和关注者规模，并把各项行为偏差的高、中、低等级写入系统提示词。GPT-4o 依据整体理性程度划分风格，前 40% 为基本面投资者，其余为技术面投资者；理性程度最高的一部分获得十倍初始资金。初始持仓由随机选取的真实投资者匿名交易模式采样生成。";

const AGENT_VIEWS = [
  ["distribution", "群体分布"],
  ["list", "列表"],
  ["workbench", "配置工作台"],
];

function AgentWorkbench({ group }) {
  const { navigate } = useRouter();
  const percent = (value) => `${Math.round(value * 100)}%`;
  return (
    <Flex direction="column" gap="4">
      <Panel
        title="当前群体"
        actions={
          <>
            <PlannedButton>行为金融测试</PlannedButton>
            <PlannedButton variant="solid">创建群体</PlannedButton>
          </>
        }
      >
        <Definition
          values={[
            ["名称", group.name],
            ["成员", `${n(group.users)} 位投资者`],
            ["数据来源", group.source],
            ["状态", group.status],
          ]}
        />
      </Panel>
      <Panel
        title="实验默认参数"
        actions={
          <Button variant="soft" color="gray" onClick={() => navigate("/studio/Experiment/new")}>
            新建实验
          </Button>
        }
      >
        <Definition
          values={[
            ["Agent 数量", n(DEFAULTS.user_count)],
            ["激活概率", percent(DEFAULTS.activate_prob)],
            ["技术面概率", percent(DEFAULTS.prob_of_technical)],
            ["并发 Worker", n(DEFAULTS.max_workers)],
            ["Agent 超时", `${DEFAULTS.agent_timeout} 秒`],
            ["随机种子", DEFAULTS.random_seed],
          ]}
        />
        <Text as="p" size="1" color="gray" mt="3">
          这里只读，新建实验时可逐项调整。
        </Text>
      </Panel>
    </Flex>
  );
}

export function AgentSpace({ catalog, refresh }) {
  const g = catalog.group;
  const { route, navigate } = useRouter();
  const view = AGENT_VIEWS.some(([id]) => id === route.subview) ? route.subview : AGENT_VIEWS[0][0];
  const [selected, setSelected] = useState(null);
  return (
    <>
      <PageHeader description={PERSONA_INTRO} />
      <Tabs.Root value={view} onValueChange={(id) => navigate(`/studio/Agent?view=${id}`, { replace: true })}>
        <Tabs.List mb="4">
          {AGENT_VIEWS.map(([id, label]) => (
            <Tabs.Trigger key={id} value={id}>
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
      {view === "distribution" && (
        <AgentDistribution runId="template" title={`${g.name} · ${n(g.users)} 位投资者`} refresh={refresh} />
      )}
      {view === "list" && <AgentExplorer runId="template" onSelect={setSelected} refresh={refresh} />}
      {view === "workbench" && <AgentWorkbench group={g} />}
      {selected && <AgentDialog runId="template" agentId={selected} onClose={() => setSelected(null)} />}
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
