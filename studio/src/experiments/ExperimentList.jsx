import React, { useMemo, useState } from "react";
import {
  Box,
  Button,
  Card,
  DropdownMenu,
  Flex,
  Grid,
  Heading,
  IconButton,
  Progress,
  SegmentedControl,
  Select,
  Table,
  Text,
  TextField,
  Tooltip,
} from "@radix-ui/themes";
import {
  DotsHorizontalIcon,
  GridIcon,
  MagnifyingGlassIcon,
  MixerHorizontalIcon,
  PlusIcon,
  RowsIcon,
} from "@radix-ui/react-icons";
import { n, runProgress, stamp, useData, usePreference } from "../lib.js";
import { Empty, ErrorCallout, PageHeader, StatusBadge, icon } from "../ui.jsx";
import { Link, useRouter } from "../router.jsx";
import { RunControlButton, RunMoreMenu, RunResultButton, useRunActions } from "./run-actions.jsx";

// 可选列：fixed 的列始终显示；默认隐藏的列可在“列设置”里打开。
const COLUMNS = [
  { id: "name", label: "别名", fixed: true },
  { id: "status", label: "状态" },
  { id: "progress", label: "进度" },
  { id: "range", label: "模拟区间" },
  { id: "agents", label: "Agent 数", numeric: true },
  { id: "snapshot", label: "市场环境", hidden: true },
  { id: "workers", label: "并发 Worker", numeric: true, hidden: true },
  { id: "seed", label: "随机种子", hidden: true },
  { id: "runId", label: "实验 ID" },
  { id: "created", label: "创建时间" },
  { id: "updated", label: "更新时间" },
  { id: "actions", label: "操作", fixed: true },
];
const DEFAULT_COLUMNS = COLUMNS.filter((c) => !c.fixed && !c.hidden).map((c) => c.id);

const STATUS_FILTERS = {
  all: ["全部状态", () => true],
  active: ["进行中", (s) => ["queued", "running", "stopping"].includes(s)],
  draft: ["草稿", (s) => s === "draft"],
  completed: ["已完成", (s) => s === "completed"],
  problem: ["失败 / 已取消", (s) => ["failed", "cancelled"].includes(s)],
};

function ProgressCell({ run }) {
  const progress = runProgress(run);
  return (
    <Flex direction="column" gap="1" minWidth="120px">
      <Progress value={progress.ratio * 100} size="1" color={run.status === "failed" ? "red" : undefined} aria-label="日期进度" />
      <Text size="1" color="gray">
        {progress.label}
      </Text>
    </Flex>
  );
}

const cell = {
  status: (run) => <StatusBadge value={run.status} />,
  progress: (run) => <ProgressCell run={run} />,
  range: (run) => `${run.config?.start_date || "—"} → ${run.config?.end_date || "—"}`,
  agents: (run) => n(run.config?.user_count),
  snapshot: (run) => run.config?.market_template || run.config?.market_snapshot_id || <Text color="gray">内置历史文件</Text>,
  workers: (run) => n(run.config?.max_workers),
  seed: (run) => run.config?.random_seed ?? "—",
  runId: (run) => <Text color="gray">{run.run_id}</Text>,
  created: (run) => stamp(run.created_at),
  updated: (run) => stamp(run.updated_at),
};

function ColumnPicker({ visible, setVisible }) {
  const toggle = (id, checked) =>
    setVisible((previous) => {
      const next = checked ? [...previous, id] : previous.filter((value) => value !== id);
      // 按列定义顺序保存，表头顺序不随勾选顺序变化。
      return COLUMNS.map((c) => c.id).filter((value) => next.includes(value));
    });
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>
        <Button variant="soft" color="gray">
          {icon(MixerHorizontalIcon)} 列设置
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content align="end">
        <DropdownMenu.Label>显示的列</DropdownMenu.Label>
        {COLUMNS.filter((c) => !c.fixed).map((c) => (
          <DropdownMenu.CheckboxItem
            key={c.id}
            checked={visible.includes(c.id)}
            onCheckedChange={(checked) => toggle(c.id, checked)}
            onSelect={(event) => event.preventDefault()}
          >
            {c.label}
          </DropdownMenu.CheckboxItem>
        ))}
        <DropdownMenu.Separator />
        <DropdownMenu.Item onSelect={() => setVisible(DEFAULT_COLUMNS)}>恢复默认</DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}

function RunTable({ runs, columns, actions }) {
  const { navigate } = useRouter();
  const shown = COLUMNS.filter((c) => c.fixed || columns.includes(c.id));
  return (
    <Card size="1" className="table-card">
      <Table.Root size="2" variant="ghost" className="run-table">
        <Table.Header>
          <Table.Row>
            {shown.map((c) => (
              <Table.ColumnHeaderCell key={c.id} justify={c.numeric ? "end" : c.id === "actions" ? "end" : "start"}>
                {c.label}
              </Table.ColumnHeaderCell>
            ))}
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {runs.map((run) => {
            const to = `/studio/runs/${encodeURIComponent(run.run_id)}`;
            return (
              <Table.Row
                key={run.run_id}
                align="center"
                className="clickable-row"
                onClick={(event) => {
                  // 行内按钮、菜单和链接自行处理点击，其余区域打开实验。
                  if (event.target.closest("a,button,[role=menuitem]")) return;
                  navigate(to);
                }}
              >
                {shown.map((c) => {
                  if (c.id === "name")
                    return (
                      <Table.RowHeaderCell key={c.id}>
                        <Link to={to} weight="medium" highContrast underline="hover">
                          {run.name}
                        </Link>
                      </Table.RowHeaderCell>
                    );
                  if (c.id === "actions")
                    return (
                      <Table.Cell key={c.id} justify="end">
                        <Flex gap="2" justify="end" align="center">
                          <RunControlButton run={run} actions={actions} size="1" />
                          <RunResultButton run={run} size="1" />
                          <RunMoreMenu run={run} actions={actions}>
                            <IconButton variant="ghost" color="gray" size="1" aria-label={`${run.name} 更多操作`}>
                              {icon(DotsHorizontalIcon)}
                            </IconButton>
                          </RunMoreMenu>
                        </Flex>
                      </Table.Cell>
                    );
                  return (
                    <Table.Cell key={c.id} justify={c.numeric ? "end" : "start"} className={c.numeric ? "tabular" : ""}>
                      {cell[c.id](run)}
                    </Table.Cell>
                  );
                })}
              </Table.Row>
            );
          })}
        </Table.Body>
      </Table.Root>
    </Card>
  );
}

function RunCards({ runs, actions }) {
  const { navigate } = useRouter();
  return (
    <Grid columns={{ initial: "1", sm: "2", lg: "3" }} gap="4">
      {runs.map((run) => {
        const to = `/studio/runs/${encodeURIComponent(run.run_id)}`;
        return (
          <Card
            key={run.run_id}
            size="2"
            className="run-card"
            onClick={(event) => {
              // 与列表行一致：卡片内按钮、菜单和链接自行处理点击，其余区域打开实验。
              // 菜单弹出的对话框经 Portal 渲染，点击仍会冒泡到卡片，也要排除。
              if (!event.currentTarget.contains(event.target) || event.target.closest("a,button,[role=menuitem]")) return;
              navigate(to);
            }}
          >
            <Flex justify="between" align="start" gap="3">
              <Box minWidth="0">
                <Heading as="h3" size="3" truncate>
                  <Link to={to} highContrast underline="hover">
                    {run.name}
                  </Link>
                </Heading>
                <Text as="p" size="1" color="gray" mt="1">
                  {cell.range(run)} · {n(run.config?.user_count)} Agents
                </Text>
                <Text as="p" size="1" color="gray">
                  ID {run.run_id}
                </Text>
              </Box>
              <StatusBadge value={run.status} />
            </Flex>
            <Box my="4">
              <ProgressCell run={run} />
            </Box>
            <Flex justify="between" align="end" gap="3">
              <Text size="1" color="gray">
                创建 {stamp(run.created_at)}
                <br />
                更新 {stamp(run.updated_at)}
              </Text>
              <Flex gap="2" align="center">
                <RunControlButton run={run} actions={actions} size="1" />
                <RunResultButton run={run} size="1" />
                <RunMoreMenu run={run} actions={actions}>
                  <IconButton variant="ghost" color="gray" size="1" aria-label={`${run.name} 更多操作`}>
                    {icon(DotsHorizontalIcon)}
                  </IconButton>
                </RunMoreMenu>
              </Flex>
            </Flex>
          </Card>
        );
      })}
    </Grid>
  );
}

export function ExperimentList({ refresh }) {
  const { navigate } = useRouter();
  const [tick, setTick] = useState(0);
  const runs = useData(`/api/runs?page_size=100`, `${refresh}:${tick}`, 5000);
  const actions = useRunActions(() => setTick((v) => v + 1));
  const [view, setView] = usePreference("twin-experiment-view", "table");
  const [columns, setColumns] = usePreference("twin-experiment-columns", DEFAULT_COLUMNS);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");

  const items = runs.data?.items || [];
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter(
      (run) =>
        STATUS_FILTERS[status][1](run.status) &&
        (!q || `${run.name} ${run.run_id}`.toLowerCase().includes(q)),
    );
  }, [items, query, status]);

  return (
    <>
      <PageHeader
        description="点击实验进入工作台查看运行进度与结果。草稿可直接运行，运行中可停止，失败的实验可重试，已停止的实验可续跑，已完成的实验可直接查看结果。"
        actions={
          <Button onClick={() => navigate("/studio/Experiment/new")}>
            {icon(PlusIcon)} 创建实验
          </Button>
        }
      />
      <Flex gap="3" align="center" wrap="wrap" mb="4" className="list-toolbar">
        <TextField.Root placeholder="搜索别名或实验 ID" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="搜索实验" className="search-field">
          <TextField.Slot>{icon(MagnifyingGlassIcon)}</TextField.Slot>
        </TextField.Root>
        <Select.Root value={status} onValueChange={setStatus}>
          <Select.Trigger aria-label="按状态筛选" />
          <Select.Content>
            {Object.entries(STATUS_FILTERS).map(([id, [label]]) => (
              <Select.Item key={id} value={id}>
                {label}
              </Select.Item>
            ))}
          </Select.Content>
        </Select.Root>
        <Box flexGrow="1" />
        {view === "table" && <ColumnPicker visible={columns} setVisible={setColumns} />}
        <SegmentedControl.Root value={view} onValueChange={setView} aria-label="展示方式">
          <SegmentedControl.Item value="table" aria-label="列表视图">
            <Tooltip content="列表">
              <Flex align="center">{icon(RowsIcon)}</Flex>
            </Tooltip>
          </SegmentedControl.Item>
          <SegmentedControl.Item value="cards" aria-label="卡片视图">
            <Tooltip content="卡片">
              <Flex align="center">{icon(GridIcon)}</Flex>
            </Tooltip>
          </SegmentedControl.Item>
        </SegmentedControl.Root>
      </Flex>
      <ErrorCallout>{actions.error || (runs.error && runs.data ? `${runs.error}，显示上次读取的数据` : null)}</ErrorCallout>
      {!runs.data ? (
        <Empty state={runs} />
      ) : !visible.length ? (
        <Card size="3">
          <Empty>
            {items.length ? "没有符合筛选条件的实验。" : "还没有实验。点击右上角“创建实验”开始第一次模拟。"}
          </Empty>
        </Card>
      ) : view === "table" ? (
        <RunTable runs={visible} columns={columns} actions={actions} />
      ) : (
        <RunCards runs={visible} actions={actions} />
      )}
    </>
  );
}
