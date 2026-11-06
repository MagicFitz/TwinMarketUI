import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  Callout,
  Card,
  Code,
  Dialog,
  Flex,
  IconButton,
  SegmentedControl,
  Table,
  Tabs,
  Text,
  TextField,
} from "@radix-ui/themes";
import { Cross2Icon, MagnifyingGlassIcon } from "@radix-ui/react-icons";
import { duration, n, sinceMs, useData } from "../lib.js";
import { Definition, Empty, StatusBadge, icon } from "../ui.jsx";
import { LLMContext } from "./LLMContextInspector.jsx";

/*
 * 智能体空间与实验工作台共用的 Agent 列表和详情弹窗。
 * 有运行时快照时提供“运行状态”数据源，否则只展示成员档案。
 */

const PIPELINE = ["state", "perception", "interpretation", "decision", "action", "commit"];
const STAGE_NAME = {
  state: "读取状态",
  perception: "感知信息",
  interpretation: "形成判断（LLM）",
  decision: "交易意图",
  action: "可执行动作",
  commit: "提交产物",
};
export const stageName = (stage) => STAGE_NAME[stage] || stage || "—";
const STATUS_ORDER = { failed: 0, retrying: 1, running: 2, pending: 3, completed: 4, skipped: 5 };
const isProblem = (agent) => ["failed", "retrying"].includes(agent.status) || Boolean(agent.error);
const RUNTIME_FILTERS = {
  all: ["全部", () => true],
  problem: ["失败/重试", isProblem],
  running: ["运行中", (agent) => agent.status === "running"],
  completed: ["已完成", (agent) => agent.status === "completed"],
};
const PAGE_SIZE = 20;

export function AgentProgressBar({ agents }) {
  const total = agents.length || 1;
  const count = (status) => agents.filter((agent) => agent.status === status).length;
  return (
    <div className="agent-progress" aria-label="Agent 完成进度">
      {["completed", "running", "retrying", "failed"].map((key) => (
        <i key={key} className={key} style={{ width: `${(100 * count(key)) / total}%` }} />
      ))}
    </div>
  );
}

function AgentError({ error, previous }) {
  if (!error) return null;
  return (
    <Callout.Root color={previous ? "amber" : "red"} size="1" role="alert">
      <Callout.Text>
        <Text weight="bold">
          {previous ? "上次失败于「" : "失败于「"}
          {stageName(error.stage)}」{error.substage ? ` · ${error.substage}` : ""} · 第 {error.attempt || 1} 次尝试
        </Text>
        <br />
        <Code size="1" color="red">
          {error.code || "Error"}
        </Code>{" "}
        {error.summary || error.message || "没有错误详情"}
      </Callout.Text>
    </Callout.Root>
  );
}

function Pipeline({ agent, now }) {
  return (
    <Flex direction="column" gap="3">
      <AgentError error={agent.error} previous={agent.status !== "failed"} />
      <div className="pipeline">
        {PIPELINE.map((id) => {
          const stage = agent.stages?.[id] || {};
          const live = ["running", "retrying"].includes(stage.status);
          return (
            <Card key={id} size="1" className={`pipeline-step ${stage.status || "pending"}`}>
              <Flex justify="between" align="center" gap="2">
                <Text size="2" weight="medium">
                  {stageName(id)}
                </Text>
                <StatusBadge value={stage.status || "pending"} />
              </Flex>
              <Text as="p" size="1" color="gray" mt="1">
                {stage.status === "failed" ? stage.error_summary || stage.message : stage.message || "等待"}
              </Text>
              <Text as="p" size="1" color="gray" className="tabular">
                {live ? `已进行 ${duration(sinceMs(stage.started_at, now))}` : duration(stage.duration_ms)}
                {stage.substage ? ` · ${stage.substage}` : ""}
              </Text>
            </Card>
          );
        })}
      </div>
    </Flex>
  );
}

function Profile({ runId, agentId }) {
  const state = useData(`/api/runs/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agentId)}`);
  if (!state.data) return <Empty state={state}>{state.error ? "该 Agent 暂无档案。" : undefined}</Empty>;
  const a = state.data;
  const positions = Object.entries(a.positions || {});
  return (
    <Flex direction="column" gap="4">
      <Definition
        values={[
          ["投资者类型", a.user_type],
          ["地区", a.location],
          ["策略", a.strategy],
          ["处置效应", a.disposition],
          ["彩票偏好", a.lottery],
          ["分散投资", a.diversification],
          ["现金", n(a.current_cash)],
          ["总资产", n(a.total_value)],
          ["收益率", a.return_rate == null ? null : `${n(a.return_rate)}%`],
        ]}
      />
      {positions.length > 0 && (
        <Table.Root size="1">
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeaderCell>持仓</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell justify="end">股数</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell justify="end">占比</Table.ColumnHeaderCell>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {positions.map(([code, item]) => (
              <Table.Row key={code}>
                <Table.Cell>{code}</Table.Cell>
                <Table.Cell justify="end" className="tabular">{n(item.shares)}</Table.Cell>
                <Table.Cell justify="end" className="tabular">{item.ratio == null ? "—" : `${item.ratio}%`}</Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Root>
      )}
    </Flex>
  );
}

export function AgentDialog({ runId, agentId, runtimeAgent = null, live = false, now = Date.now(), onClose }) {
  const hasRun = runId !== "template";
  const tabs = [
    ["profile", "档案"],
    ...(hasRun ? [["context", "上下文"]] : []),
    ...(runtimeAgent ? [["pipeline", "流水线"]] : []),
  ];
  // 出问题的 Agent 先看它卡在哪一步。
  const [tab, setTab] = useState(runtimeAgent && isProblem(runtimeAgent) ? "pipeline" : "profile");
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Content maxWidth="760px" aria-describedby={undefined}>
        <Flex justify="between" align="center" gap="3" mb="3">
          <Flex align="center" gap="2" minWidth="0">
            <Dialog.Title size="4" mb="0" className="tabular">
              {agentId}
            </Dialog.Title>
            {runtimeAgent && isProblem(runtimeAgent) && <StatusBadge value={runtimeAgent.status} />}
          </Flex>
          <Dialog.Close>
            <IconButton variant="ghost" color="gray" aria-label="关闭">
              {icon(Cross2Icon)}
            </IconButton>
          </Dialog.Close>
        </Flex>
        {tabs.length > 1 ? (
          <Tabs.Root value={tab} onValueChange={setTab}>
            <Tabs.List size="1">
              {tabs.map(([id, label]) => (
                <Tabs.Trigger key={id} value={id}>
                  {label}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
          </Tabs.Root>
        ) : null}
        <Box pt="3">
          {tab === "profile" && <Profile runId={runId} agentId={agentId} />}
          {tab === "context" && <LLMContext runId={runId} agentId={agentId} live={live} />}
          {tab === "pipeline" && runtimeAgent && <Pipeline agent={runtimeAgent} now={now} />}
        </Box>
      </Dialog.Content>
    </Dialog.Root>
  );
}

function RuntimeTable({ agents, onSelect, timeout, now }) {
  const [filter, setFilter] = useState("all");
  const visible = agents
    .filter(RUNTIME_FILTERS[filter][1])
    .sort(
      (a, b) =>
        (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) ||
        String(a.stage_started_at || "").localeCompare(String(b.stage_started_at || "")),
    );
  return (
    <>
      <Flex align="center" gap="3" wrap="wrap" mb="3">
        <SegmentedControl.Root value={filter} onValueChange={setFilter} size="1">
          {Object.entries(RUNTIME_FILTERS).map(([id, [label, match]]) => (
            <SegmentedControl.Item key={id} value={id}>
              {label} {agents.filter(match).length}
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
      </Flex>
      <AgentProgressBar agents={agents} />
      {visible.length ? (
        <Table.Root size="1" mt="3" className="agent-table">
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeaderCell>Agent ID</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>状态</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>当前阶段</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell justify="end">耗时</Table.ColumnHeaderCell>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {visible.map((agent) => {
              const running = ["running", "retrying"].includes(agent.status);
              const age = running ? sinceMs(agent.stage_started_at, now) : null;
              const slow = age != null && age > timeout * 800;
              const stageNow = agent.stages?.[agent.current_stage] || {};
              const problem =
                agent.error ||
                (agent.status === "retrying"
                  ? { code: stageNow.error_code, summary: stageNow.error_summary, message: stageNow.message }
                  : null);
              return (
                <Table.Row
                  key={agent.agent_id}
                  className="clickable-row"
                  onClick={() => onSelect(agent.agent_id)}
                >
                  <Table.RowHeaderCell>
                    <Text weight="medium" className="tabular">
                      {agent.agent_id}
                    </Text>
                    {problem && (
                      <Text as="div" size="1" color={agent.status === "failed" ? "red" : "amber"} className="clip">
                        {agent.error && agent.status !== "failed" ? "上次失败 · " : ""}
                        {problem.code || "Error"}：{problem.summary || problem.message}
                      </Text>
                    )}
                  </Table.RowHeaderCell>
                  <Table.Cell>
                    <StatusBadge value={agent.status} />
                  </Table.Cell>
                  <Table.Cell>
                    <Text size="1" color="gray">
                      {agent.status === "completed"
                        ? "—"
                        : stageName(agent.status === "failed" ? agent.error?.stage : agent.current_stage)}
                      {agent.attempt > 1 ? ` · 第 ${agent.attempt} 次` : ""}
                    </Text>
                  </Table.Cell>
                  <Table.Cell justify="end">
                    <Text size="1" color={slow ? "amber" : "gray"} className="tabular">
                      {running ? duration(age) : ""}
                      {slow ? " · 接近超时" : ""}
                    </Text>
                  </Table.Cell>
                </Table.Row>
              );
            })}
          </Table.Body>
        </Table.Root>
      ) : (
        <Empty>没有 Agent</Empty>
      )}
    </>
  );
}

function ProfileTable({ runId, refresh, onSelect }) {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  // 搜索输入经 250ms 防抖后才更新查询参数，减少输入过程中的 API 请求。
  useEffect(() => {
    const timer = setTimeout(() => {
      setQ(search);
      setPage(1);
    }, 250);
    return () => clearTimeout(timer);
  }, [search]);
  const state = useData(
    `/api/runs/${encodeURIComponent(runId)}/agents?q=${encodeURIComponent(q)}&page=${page}`,
    refresh,
  );
  const pages = Math.max(1, Math.ceil((state.data?.total || 0) / PAGE_SIZE));
  return (
    <>
      <Flex align="center" gap="3" wrap="wrap" mb="3">
        <TextField.Root
          size="2"
          placeholder="ID、类型或地区"
          aria-label="搜索成员"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="search-field"
        >
          <TextField.Slot>{icon(MagnifyingGlassIcon)}</TextField.Slot>
        </TextField.Root>
      </Flex>
      {state.error && (
        <Text as="p" size="1" color="amber">
          {state.error}
        </Text>
      )}
      {state.data ? (
        <>
          {state.data.items.length ? (
            <Table.Root size="1" className="agent-table">
              <Table.Header>
                <Table.Row>
                  <Table.ColumnHeaderCell>Agent ID</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell>类型 / 地区</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell>处置效应</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell>彩票偏好</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell justify="end">总资产</Table.ColumnHeaderCell>
                  <Table.ColumnHeaderCell justify="end">收益率</Table.ColumnHeaderCell>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {state.data.items.map((a) => (
                  <Table.Row
                    key={a.user_id}
                    className="clickable-row"
                    onClick={() => onSelect(a.user_id)}
                  >
                    <Table.RowHeaderCell>
                      <Text weight="medium" className="tabular">
                        {a.user_id}
                      </Text>
                    </Table.RowHeaderCell>
                    <Table.Cell>
                      {a.user_type}
                      <Text as="div" size="1" color="gray">
                        {a.location}
                      </Text>
                    </Table.Cell>
                    <Table.Cell>{a.disposition}</Table.Cell>
                    <Table.Cell>{a.lottery}</Table.Cell>
                    <Table.Cell justify="end" className="tabular">
                      {n(a.total_value)}
                    </Table.Cell>
                    <Table.Cell justify="end" className="tabular">
                      {a.return_rate == null ? "—" : `${n(a.return_rate)}%`}
                    </Table.Cell>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          ) : (
            <Empty>没有匹配的成员</Empty>
          )}
          <Flex justify="between" align="center" gap="3" mt="3" wrap="wrap">
            <Text size="1" color="gray" className="tabular">
              {n(state.data.total)}
            </Text>
            <Flex align="center" gap="3">
              <Button size="1" variant="soft" color="gray" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
                上一页
              </Button>
              <Text size="1" className="tabular">
                {page} / {pages}
              </Text>
              <Button size="1" variant="soft" color="gray" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
                下一页
              </Button>
            </Flex>
          </Flex>
        </>
      ) : (
        <Empty state={state} />
      )}
    </>
  );
}

export function AgentExplorer({ runId = "template", runtime = null, live = false, now = Date.now(), timeout = 300, onSelect, refresh = 0 }) {
  const runtimeAgents = runtime?.agents || [];
  const [source, setSource] = useState(live && runtimeAgents.length ? "runtime" : "profiles");
  // 运行开始后第一次出现 Agent 事件时，自动切到运行状态。
  useEffect(() => {
    if (live && runtimeAgents.length && source === "profiles") setSource("runtime");
  }, [live && runtimeAgents.length > 0]);
  return (
    <Card size="2">
      {runtime && (
        <Flex mb="3">
          <SegmentedControl.Root value={source} onValueChange={setSource}>
            <SegmentedControl.Item value="profiles">档案</SegmentedControl.Item>
            <SegmentedControl.Item value="runtime">运行状态</SegmentedControl.Item>
          </SegmentedControl.Root>
        </Flex>
      )}
      {source === "runtime" && runtime ? (
        <RuntimeTable agents={runtimeAgents} onSelect={onSelect} timeout={timeout} now={now} />
      ) : (
        <ProfileTable runId={runId} refresh={refresh} onSelect={onSelect} />
      )}
    </Card>
  );
}
