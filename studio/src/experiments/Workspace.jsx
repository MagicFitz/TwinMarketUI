import React, { useEffect, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Card,
  Flex,
  Grid,
  Heading,
  IconButton,
  SegmentedControl,
  Table,
  Tabs,
  Text,
} from "@radix-ui/themes";
import { DotsHorizontalIcon, DownloadIcon } from "@radix-ui/react-icons";
import { fetchJson, isActiveRun, n, stamp, useNow, STATUS } from "../lib.js";
import { Definition, Empty, ErrorCallout, Panel, Stat, StatusBadge, icon } from "../ui.jsx";
import { useRouter } from "../router.jsx";
import { AgentExplorer, AgentProgressBar, stageName } from "../agents/AgentExplorer.jsx";
import { MarketChart } from "../MarketChart.jsx";
import { RunControlButton, RunMoreMenu, useRunActions } from "./run-actions.jsx";

// 工作台的 tab 与 URL 段一一对应；App 的面包屑也读取这份定义。
export const WORKSPACE_TABS = [
  ["progress", "运行进度"],
  ["agents", "Agents"],
  ["results", "结果"],
  ["logs", "日志与诊断"],
  ["config", "配置"],
];

const DAY = [
  ["prepare", "环境准备"],
  ["agents", "Agent 决策"],
  ["market", "市场撮合"],
  ["social", "社交互动"],
  ["commit", "提交当日"],
];

function currentStage(day) {
  const entries = Object.entries(day?.stages || {});
  const running = entries.find(([, value]) => value?.status === "running");
  if (running) return running[1].substage || running[1].message || DAY.find(([id]) => id === running[0])?.[1] || running[0];
  const pending = entries.find(([, value]) => ["pending", "waiting"].includes(value?.status));
  return pending ? DAY.find(([id]) => id === pending[0])?.[1] || pending[0] : day?.date ? "当日已完成" : "等待启动";
}

// 运行记录与运行时快照：活动中每 2 秒刷新，结束后放缓到 10 秒。
function useRunState(runId, refresh) {
  const [state, setState] = useState({ run: null, snapshot: null, error: "" });
  const [tick, setTick] = useState(0);
  const active = isActiveRun(state.run?.status);
  useEffect(() => {
    let alive = true;
    const read = async () => {
      try {
        const [run, snapshot] = await Promise.all([
          fetchJson(`/api/runs/${encodeURIComponent(runId)}`),
          fetchJson(`/api/runs/${encodeURIComponent(runId)}/runtime`),
        ]);
        if (alive) setState({ run, snapshot, error: "" });
      } catch (e) {
        if (alive) setState((previous) => ({ ...previous, error: e.message }));
      }
    };
    read();
    const timer = setInterval(read, active || !state.run ? 2000 : 10000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [runId, refresh, tick, active]);
  return { ...state, reload: () => setTick((v) => v + 1) };
}

// 运行事件流：SSE 断线后指数退避重连，游标保存在 sessionStorage 避免重复事件。
function useRunEvents(runId) {
  const [events, setEvents] = useState([]);
  const [streamState, setStreamState] = useState("连接中");
  useEffect(() => {
    const key = `twinmarket:studio:event-cursor:${runId}`;
    let cursor = Number(sessionStorage.getItem(key) || 0);
    let source, timer, closed = false, retry = 0;
    const seen = new Set();
    const connect = () => {
      if (closed) return;
      setStreamState(retry ? "重连中" : "连接中");
      source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events?last_event_id=${cursor}`);
      source.onopen = () => {
        retry = 0;
        setStreamState("已连接");
      };
      source.addEventListener("runtime", (event) => {
        try {
          const item = JSON.parse(event.data);
          if (!Number.isInteger(item.event_id) || item.event_id <= cursor || seen.has(item.event_id)) return;
          seen.add(item.event_id);
          if (seen.size > 500) seen.delete(seen.values().next().value);
          cursor = item.event_id;
          sessionStorage.setItem(key, String(cursor));
          setEvents((previous) => [item, ...previous].slice(0, 30));
        } catch {
          /* 忽略无法解析的事件 */
        }
      });
      source.onerror = () => {
        source.close();
        if (closed) return;
        setStreamState("重连中");
        timer = setTimeout(connect, Math.min(15000, 1000 * 2 ** Math.min(retry++, 4)));
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      source?.close();
    };
  }, [runId]);
  return { events, streamState };
}

function DayCycle({ day }) {
  return (
    <Panel title="当日流程" actions={<Text size="2" color="gray">{day.date || "尚未开始"}</Text>}>
      <Grid columns={{ initial: "2", sm: "3", md: "5" }} gap="3">
        {DAY.map(([id, name]) => {
          const stage = day.stages?.[id] || {};
          return (
            <Card key={id} size="1" className={`day-step ${stage.status || "pending"}`}>
              <Flex justify="between" align="center" gap="2">
                <Text size="2" weight="medium">
                  {name}
                </Text>
                <StatusBadge value={stage.status || "pending"} />
              </Flex>
              <Text as="p" size="1" color="gray" mt="1" className="clip">
                {stage.substage || stage.message || "等待事件"}
              </Text>
            </Card>
          );
        })}
      </Grid>
    </Panel>
  );
}

function ProgressTab({ run, snapshot, events, streamState, openAgent }) {
  const counts = snapshot.agent_counts || {};
  const agents = snapshot.agents || [];
  const attention = agents.filter((agent) => ["failed", "retrying"].includes(agent.status) || agent.error);
  return (
    <Flex direction="column" gap="4">
      <Grid columns={{ initial: "2", md: "4" }} gap="3">
        <Stat label="模拟日期" value={snapshot.day.date || "—"} hint={`区间 ${run.config.start_date} → ${run.config.end_date}`} />
        <Stat label="当前阶段" value={currentStage(snapshot.day)} />
        <Stat
          label="Agent"
          value={`${counts.completed || 0} / ${agents.length || run.config.user_count || 0}`}
          hint={`${(counts.running || 0) + (counts.retrying || 0)} 进行中 · ${counts.failed || 0} 失败`}
        />
        <Stat label="事件流" value={`#${snapshot.last_event_id ?? 0}`} hint={streamState} />
      </Grid>
      <DayCycle day={snapshot.day} />
      <Grid columns={{ initial: "1", md: "2" }} gap="4">
        <Panel title="需要关注的 Agent" hint="失败、重试中或带有错误记录的 Agent。点击查看决策流水线与 LLM 上下文。">
          <AgentProgressBar agents={agents} />
          {attention.length ? (
            <Flex direction="column" gap="2" mt="3">
              {attention.slice(0, 8).map((agent) => (
                <button key={agent.agent_id} className="list-button" onClick={() => openAgent(agent.agent_id)}>
                  <Flex justify="between" align="center" gap="2">
                    <Text size="2" weight="medium" className="tabular">
                      {agent.agent_id}
                    </Text>
                    <StatusBadge value={agent.status} />
                  </Flex>
                  <Text as="div" size="1" color="gray" className="clip">
                    {stageName(agent.error?.stage || agent.current_stage)} · {agent.error?.summary || agent.error?.message || "重试中"}
                  </Text>
                </button>
              ))}
            </Flex>
          ) : (
            <Text as="p" size="2" color="gray" mt="3">
              {agents.length ? "当前没有失败或重试的 Agent。" : "运行开始后这里会显示异常 Agent。"}
            </Text>
          )}
        </Panel>
        <Panel title="最近事件" actions={<Badge color={streamState === "已连接" ? "green" : "gray"} variant="soft">{streamState}</Badge>}>
          {events.length ? (
            <Flex direction="column" gap="1" className="event-list">
              {events.map((item) => (
                <button key={item.event_id} className="list-button" disabled={!item.agent_id} onClick={() => item.agent_id && openAgent(item.agent_id)}>
                  <Flex justify="between" align="center" gap="2">
                    <Text size="1" color="gray" className="tabular">
                      #{item.event_id} · {item.scope}/{item.stage}
                    </Text>
                    {STATUS[item.event?.replace("stage_", "")] && <StatusBadge value={item.event.replace("stage_", "")} />}
                  </Flex>
                  <Text as="div" size="2" className="clip">
                    {item.message}
                  </Text>
                </button>
              ))}
            </Flex>
          ) : (
            <Text as="p" size="2" color="gray">
              高层事件会在运行开始后出现。
            </Text>
          )}
        </Panel>
      </Grid>
      {snapshot.today_summary && Object.keys(snapshot.today_summary).length > 0 && (
        <Panel title="当日汇总">
          <Grid columns={{ initial: "2", md: "4" }} gap="3">
            {Object.entries(snapshot.today_summary).map(([key, value]) => (
              <Box key={key}>
                <Text as="div" size="5" weight="bold" className="tabular">
                  {n(value)}
                </Text>
                <Text size="1" color="gray">
                  {STATUS[key]?.[0] || key}
                </Text>
              </Box>
            ))}
          </Grid>
        </Panel>
      )}
    </Flex>
  );
}

const RESULT_KINDS = [
  ["summary", "每日汇总"],
  ["trades", "成交明细"],
  ["orders", "委托"],
  ["agents", "Agent 结果"],
  ["social", "社交"],
];

function ResultTable({ runId }) {
  const [kind, setKind] = useState("summary");
  const [state, setState] = useState({ data: null, error: "" });
  useEffect(() => {
    setState({ data: null, error: "" });
    fetchJson(`/api/runs/${encodeURIComponent(runId)}/results/${kind}`)
      .then((data) => setState({ data, error: "" }))
      .catch((e) => setState({ data: null, error: e.message }));
  }, [runId, kind]);
  const items = state.data?.items || [];
  const keys = items.length ? Object.keys(items[0]) : [];
  const exportable = ["trades", "orders"].includes(kind);
  return (
    <Panel
      title="结果数据表"
      hint={`来源：${state.data?.source || "simulation artifacts"}。只读取真实产物，不做补全。`}
      actions={
        <>
          <SegmentedControl.Root size="1" value={kind} onValueChange={setKind}>
            {RESULT_KINDS.map(([id, label]) => (
              <SegmentedControl.Item key={id} value={id}>
                {label}
              </SegmentedControl.Item>
            ))}
          </SegmentedControl.Root>
          {exportable &&
            ["csv", "json"].map((format) => (
              <Button key={format} asChild size="1" variant="soft" color="gray">
                <a href={`/api/runs/${encodeURIComponent(runId)}/results/${kind}/export?format=${format}`}>
                  {icon(DownloadIcon, 12)} {format.toUpperCase()}
                </a>
              </Button>
            ))}
        </>
      }
    >
      {state.error ? (
        <Empty>暂无此类产物 · {state.error}</Empty>
      ) : !state.data ? (
        <Empty state={{ loading: true }} />
      ) : items.length ? (
        <div className="scroll-table">
          <Table.Root size="1">
            <Table.Header>
              <Table.Row>
                {keys.map((key) => (
                  <Table.ColumnHeaderCell key={key}>{key}</Table.ColumnHeaderCell>
                ))}
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {items.map((row, index) => (
                <Table.Row key={index}>
                  {keys.map((key) => (
                    <Table.Cell key={key} className="clip-cell">
                      {typeof row[key] === "object" && row[key] !== null ? JSON.stringify(row[key]) : String(row[key] ?? "—")}
                    </Table.Cell>
                  ))}
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        </div>
      ) : (
        <Empty>当前尚无此类真实产物。</Empty>
      )}
    </Panel>
  );
}

function ResultsTab({ run, refresh }) {
  return (
    <Flex direction="column" gap="4">
      {run.status !== "completed" && (
        <Text size="2" color="gray">
          实验{STATUS[run.status]?.[0] || run.status}，以下为截至目前已写入的产物。
        </Text>
      )}
      <MarketChart run={run.run_id} refresh={refresh} />
      <ResultTable runId={run.run_id} />
    </Flex>
  );
}

function RuntimeLog({ runId, status }) {
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const read = async () => {
      try {
        const data = await fetchJson(`/api/runs/${encodeURIComponent(runId)}/logs?page=1&page_size=100`);
        if (alive) {
          setLogs(data.items || []);
          setError("");
        }
      } catch (reason) {
        if (alive) setError(reason.message);
      }
    };
    read();
    const timer = isActiveRun(status) ? setInterval(read, 1000) : null;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [runId, status]);
  return (
    <Panel
      title="运行日志"
      hint="仅显示经过后端脱敏的运行进度；运行期间每秒刷新。"
      actions={<Badge color={isActiveRun(status) ? "green" : "gray"} variant="soft">{isActiveRun(status) ? "LIVE" : "ARCHIVE"}</Badge>}
    >
      <div className="terminal" role="log" aria-live="polite">
        <div className="terminal-command">$ twinmarket simulation --run {runId}</div>
        {logs.length ? (
          logs.map((item, index) => (
            <div key={`${index}-${item}`}>
              <span className="terminal-prompt">›</span>
              {item}
            </div>
          ))
        ) : (
          <div className="terminal-muted">
            <span className="terminal-prompt">›</span>
            {isActiveRun(status) ? "等待安全运行日志写入…" : "当前没有可显示的运行日志。"}
          </div>
        )}
        {error && (
          <div className="terminal-error">
            <span className="terminal-prompt">!</span>日志读取失败：{error}
          </div>
        )}
      </div>
    </Panel>
  );
}

function LogsTab({ run, snapshot, openAgent }) {
  const diagnostics = snapshot.diagnostics || [];
  return (
    <Flex direction="column" gap="4">
      <Grid columns={{ initial: "1", md: "2" }} gap="4">
        <Panel title="运行状态">
          <Definition
            values={[
              ["状态", <StatusBadge value={run.status} />],
              ["健康度", <StatusBadge value={run.health} />],
              ["PID", run.pid || "—"],
              ["退出码", run.exit_code ?? "—"],
              ["最近心跳", stamp(run.last_heartbeat)],
              ["失败原因", run.failure_reason || "没有未处理的运行级错误。"],
            ]}
          />
        </Panel>
        <Panel title="诊断事件" hint="失败、重试与慢阶段事件。">
          {diagnostics.length ? (
            <Flex direction="column" gap="2" className="event-list">
              {diagnostics.map((item) => (
                <button key={item.event_id} className="list-button" disabled={!item.agent_id} onClick={() => item.agent_id && openAgent(item.agent_id)}>
                  <Text as="div" size="2" weight="medium">
                    {item.agent_id ? `Agent ${item.agent_id} · ` : ""}
                    {item.scope}/{stageName(item.stage)}
                    {item.substage ? ` · ${item.substage}` : ""} · {item.error_code || "慢阶段"}
                  </Text>
                  <Text as="div" size="1" color="gray">
                    {item.message}
                  </Text>
                  {item.error_summary && (
                    <Text as="div" size="1" color="red" className="pre-wrap">
                      {item.error_summary}
                    </Text>
                  )}
                </button>
              ))}
            </Flex>
          ) : (
            <Text size="2" color="gray">
              当前没有失败、重试或慢阶段事件。
            </Text>
          )}
        </Panel>
      </Grid>
      <RuntimeLog runId={run.run_id} status={run.status} />
    </Flex>
  );
}

const CONFIG_LABELS = {
  user_count: "Agent 数量",
  start_date: "起始日期",
  end_date: "结束日期",
  max_workers: "并发 Worker",
  agent_timeout: "Agent 超时（秒）",
  activate_prob: "激活概率",
  prob_of_technical: "技术面概率",
  random_seed: "随机种子",
  market_data_mode: "市场数据模式",
  market_template: "市场模板",
  data_version: "数据版本",
  market_snapshot_id: "市场快照（旧版）",
  allow_missing_news: "允许无新闻",
};

function ConfigTab({ run, actions }) {
  const config = run.config || {};
  const known = Object.keys(CONFIG_LABELS).filter((key) => key in config);
  const rest = Object.keys(config).filter((key) => !(key in CONFIG_LABELS));
  const show = (value) => (typeof value === "boolean" ? (value ? "是" : "否") : typeof value === "object" && value !== null ? JSON.stringify(value) : value === null || value === "" ? "—" : String(value));
  return (
    <Grid columns={{ initial: "1", md: "2" }} gap="4" align="start">
      <Panel
        title="实验参数"
        hint="创建时冻结；已启动的实验不能修改，可复制为新实验后调整。"
        actions={
          <Button size="1" variant="soft" color="gray" onClick={() => actions.duplicate(run)}>
            复制为新实验
          </Button>
        }
      >
        <Definition values={known.map((key) => [CONFIG_LABELS[key], show(config[key])])} />
      </Panel>
      <Flex direction="column" gap="4">
        <Panel title="生命周期">
          <Definition
            values={[
              ["实验 ID", run.run_id],
              ["别名", run.name],
              ["创建时间", stamp(run.created_at)],
              ["开始时间", stamp(run.started_at)],
              ["结束时间", stamp(run.ended_at)],
              ["描述", run.description || "—"],
            ]}
          />
        </Panel>
        {rest.length > 0 && (
          <Panel title="其他配置">
            <Definition size="1" values={rest.map((key) => [key, show(config[key])])} />
          </Panel>
        )}
      </Flex>
    </Grid>
  );
}

export function Workspace({ runId, tab, agent, refresh }) {
  const { navigate } = useRouter();
  const { run, snapshot, error, reload } = useRunState(runId, refresh);
  const { events, streamState } = useRunEvents(runId);
  const actions = useRunActions(reload);
  const now = useNow(isActiveRun(run?.status));
  const base = `/studio/runs/${encodeURIComponent(runId)}`;
  // 未指定 tab 时按状态选择：已完成看结果，其余看运行进度。
  const current = tab || (run?.status === "completed" ? "results" : "progress");
  const go = (next, query = "") => navigate(`${base}/${next}${query}`, { replace: true });
  const openAgent = (id) => navigate(`${base}/agents${id ? `?agent=${encodeURIComponent(id)}` : ""}`);

  if (!run || !snapshot)
    return error ? <ErrorCallout>无法读取实验：{error}</ErrorCallout> : <Empty state={{ loading: true }} />;
  const live = isActiveRun(run.status);
  return (
    <>
      <Flex justify="between" align="start" gap="4" wrap="wrap" mb="4">
        <Box minWidth="0">
          <Flex align="center" gap="2" wrap="wrap">
            <Heading as="h1" size="6">
              {run.name}
            </Heading>
            <StatusBadge value={run.status} size="2" />
            {run.health && run.health !== "healthy" && <StatusBadge value={run.health} size="2" />}
          </Flex>
          <Text as="p" size="2" color="gray" mt="1">
            ID {run.run_id} · {run.config.start_date} → {run.config.end_date} · {n(run.config.user_count)} Agents ·{" "}
            {run.config.market_template ? `${run.config.market_template} · 数据版本 ${run.config.data_version}` : run.config.market_snapshot_id || "内置历史文件"} · 创建于 {stamp(run.created_at)}
          </Text>
        </Box>
        <Flex gap="2" align="center">
          <RunControlButton run={run} actions={actions} />
          <RunMoreMenu run={run} actions={actions} onDeleted={() => navigate("/studio/Experiment", { replace: true })}>
            <IconButton variant="soft" color="gray" aria-label="更多操作">
              {icon(DotsHorizontalIcon)}
            </IconButton>
          </RunMoreMenu>
        </Flex>
      </Flex>
      <ErrorCallout>{actions.error || (error ? `${error}，显示上次读取的数据` : null)}</ErrorCallout>
      <Tabs.Root value={current} onValueChange={(next) => go(next)}>
        <Tabs.List mb="4">
          {WORKSPACE_TABS.map(([id, label]) => (
            <Tabs.Trigger key={id} value={id}>
              {label}
              {id === "agents" && snapshot.agent_counts?.failed ? (
                <Badge color="red" variant="solid" radius="full" ml="2" size="1">
                  {snapshot.agent_counts.failed}
                </Badge>
              ) : null}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>
      {current === "progress" && <ProgressTab run={run} snapshot={snapshot} events={events} streamState={streamState} openAgent={openAgent} />}
      {current === "agents" && (
        <AgentExplorer
          runId={runId}
          runtime={snapshot}
          live={live}
          now={now}
          timeout={run.config?.agent_timeout ?? 300}
          selected={agent}
          onSelect={(id) => go("agents", id ? `?agent=${encodeURIComponent(id)}` : "")}
          refresh={refresh}
        />
      )}
      {current === "results" && <ResultsTab run={run} refresh={refresh} />}
      {current === "logs" && <LogsTab run={run} snapshot={snapshot} openAgent={openAgent} />}
      {current === "config" && <ConfigTab run={run} actions={actions} />}
    </>
  );
}
