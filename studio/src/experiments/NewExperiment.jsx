import React, { useEffect, useRef, useState } from "react";
import { Box, Button, Card, Flex, Grid, Heading, Separator, Text, TextArea, TextField } from "@radix-ui/themes";
import { ChevronDownIcon, ChevronRightIcon } from "@radix-ui/react-icons";
import { fetchJson, postJson } from "../lib.js";
import { ErrorCallout, Notice, PageHeader, icon } from "../ui.jsx";
import { useRouter } from "../router.jsx";
import { RunMarketSnapshot } from "../MarketData.jsx";

const DEFAULTS = {
  name: "10 Agent 观察运行",
  description: "",
  user_count: 10,
  start_date: "2023-06-15",
  end_date: "2023-06-16",
  max_workers: 1,
  agent_timeout: 300,
  activate_prob: 0.2,
  prob_of_technical: 0.3,
  random_seed: 20260916,
  market_snapshot_id: undefined,
  allow_missing_news: false,
};
// 复制实验时只带入表单里可编辑的参数，其余配置由后端重新规范化。
const EDITABLE = Object.keys(DEFAULTS);

function Field({ label, hint, children }) {
  return (
    <Flex asChild direction="column" gap="1">
      <label>
        <Text size="2" weight="medium">
          {label}
        </Text>
        {children}
        {hint && (
          <Text size="1" color="gray">
            {hint}
          </Text>
        )}
      </label>
    </Flex>
  );
}

function Section({ title, description, children }) {
  return (
    <Grid columns={{ initial: "1", md: "220px 1fr" }} gap={{ initial: "3", md: "6" }} py="5">
      <Box>
        <Heading as="h2" size="3">
          {title}
        </Heading>
        {description && (
          <Text as="p" size="1" color="gray" mt="1">
            {description}
          </Text>
        )}
      </Box>
      <Flex direction="column" gap="4" minWidth="0">
        {children}
      </Flex>
    </Grid>
  );
}

export function NewExperiment({ from }) {
  const { navigate, back, setGuard } = useRouter();
  const [form, setForm] = useState(DEFAULTS);
  const [source, setSource] = useState(null);
  const [marketValid, setMarketValid] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const dirty = useRef(false);

  // ?from=<runId>：以已有实验的配置作为起点。
  useEffect(() => {
    if (!from) return;
    fetchJson(`/api/runs/${encodeURIComponent(from)}`)
      .then((run) => {
        setSource(run);
        const config = Object.fromEntries(EDITABLE.filter((key) => run.config?.[key] !== undefined).map((key) => [key, run.config[key]]));
        setForm((previous) => ({
          ...previous,
          ...config,
          market_snapshot_id: run.config?.market_snapshot_id || "",
          name: `${run.name} 副本`,
          description: run.description || "",
        }));
      })
      .catch((reason) => setError(`无法读取源实验：${reason.message}`));
  }, [from]);

  // 离开页面时清除确认，避免影响其他页面的跳转。
  useEffect(() => () => setGuard(false), []);

  const change = (key, value) => {
    setForm((previous) => ({ ...previous, [key]: value }));
    if (!dirty.current) {
      dirty.current = true;
      setGuard(true);
    }
  };
  const number = (key) => (event) => change(key, event.target.value === "" ? "" : Number(event.target.value));
  const cancel = () => back("/studio/Experiment");

  async function submit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const { name, description, ...config } = form;
      const run = await postJson("/api/runs", {
        name,
        description,
        config: {
          ...config,
          market_data_mode: config.market_snapshot_id ? "snapshot" : "legacy",
          market_snapshot_id: config.market_snapshot_id || null,
        },
      });
      // 用工作台替换创建页的历史记录：在工作台点“返回”不会回到已提交的表单。
      navigate(`/studio/runs/${encodeURIComponent(run.run_id)}`, { replace: true, force: true });
    } catch (reason) {
      setError(reason.message);
    } finally {
      setSaving(false);
    }
  }

  const invalidDates = form.start_date > form.end_date;
  return (
    <form onSubmit={submit} className="new-experiment">
      <PageHeader
        title="新建实验"
        description={
          source
            ? `基于“${source.name}”的配置创建，可修改后再提交。`
            : "创建后会冻结非敏感参数与输入文件指纹，并进入实验工作台；需要你再点击“运行”才会开始模拟。"
        }
      />
      <Card size="3">
        <Section title="基本信息" description="名称会显示在实验列表和面包屑中。">
          <Field label="实验名称">
            <TextField.Root required value={form.name} onChange={(e) => change("name", e.target.value)} />
          </Field>
          <Field label="描述（可选）">
            <TextArea value={form.description} onChange={(e) => change("description", e.target.value)} rows={2} placeholder="记录本次实验的假设或变量" />
          </Field>
        </Section>
        <Separator size="4" />
        <Section title="规模与日期" description="模拟区间按交易日推进，休市日会被跳过。">
          <Grid columns={{ initial: "1", sm: "2" }} gap="4">
            <Field label="Agent 数量">
              <TextField.Root required type="number" min="1" max="1000" value={form.user_count} onChange={number("user_count")} />
            </Field>
            <Field label="并发 Worker">
              <TextField.Root required type="number" min="1" value={form.max_workers} onChange={number("max_workers")} />
            </Field>
            <Field label="起始日期">
              <TextField.Root required type="date" value={form.start_date} onChange={(e) => change("start_date", e.target.value)} />
            </Field>
            <Field label="结束日期">
              <TextField.Root required type="date" min={form.start_date} value={form.end_date} onChange={(e) => change("end_date", e.target.value)} />
            </Field>
          </Grid>
          {invalidDates && <ErrorCallout>结束日期不能早于起始日期。</ErrorCallout>}
        </Section>
        <Separator size="4" />
        <Section title="市场环境" description="实验创建时固定所用快照，之后更新市场数据不影响本实验。">
          <RunMarketSnapshot
            value={form.market_snapshot_id}
            onChange={(value) =>
              // 首次加载时组件会自动选中默认快照，这不算用户修改。
              form.market_snapshot_id === undefined
                ? setForm((previous) => ({ ...previous, market_snapshot_id: value }))
                : change("market_snapshot_id", value)
            }
            startDate={form.start_date}
            endDate={form.end_date}
            allowMissingNews={!!form.allow_missing_news}
            onAllowMissingNews={(value) => change("allow_missing_news", value)}
            onValidity={setMarketValid}
          />
        </Section>
        <Separator size="4" />
        <Section title="高级参数" description="影响 Agent 激活与决策的随机性。">
          <Box>
            <Button type="button" variant="ghost" color="gray" onClick={() => setAdvanced((v) => !v)} aria-expanded={advanced}>
              {icon(advanced ? ChevronDownIcon : ChevronRightIcon)}
              {advanced ? "收起高级参数" : "展开高级参数"}
            </Button>
          </Box>
          {advanced && (
            <Grid columns={{ initial: "1", sm: "2" }} gap="4">
              <Field label="激活概率" hint="每个交易日被激活参与决策的 Agent 比例。">
                <TextField.Root type="number" min="0" max="1" step="0.05" value={form.activate_prob} onChange={number("activate_prob")} />
              </Field>
              <Field label="技术面概率">
                <TextField.Root type="number" min="0" max="1" step="0.05" value={form.prob_of_technical} onChange={number("prob_of_technical")} />
              </Field>
              <Field label="Agent 超时（秒）">
                <TextField.Root type="number" min="30" max="3600" value={form.agent_timeout} onChange={number("agent_timeout")} />
              </Field>
              <Field label="随机种子">
                <TextField.Root type="number" value={form.random_seed} onChange={number("random_seed")} />
              </Field>
            </Grid>
          )}
        </Section>
        {!marketValid && form.market_snapshot_id !== undefined && (
          <Notice>市场环境尚未通过校验，请检查快照覆盖的日期与新闻选项。</Notice>
        )}
        <ErrorCallout>{error}</ErrorCallout>
        <Separator size="4" />
        <Flex justify="end" gap="3" pt="5" className="form-actions">
          <Button type="button" variant="soft" color="gray" onClick={cancel}>
            取消
          </Button>
          <Button type="submit" loading={saving} disabled={saving || !marketValid || invalidDates}>
            创建实验
          </Button>
        </Flex>
      </Card>
    </form>
  );
}
