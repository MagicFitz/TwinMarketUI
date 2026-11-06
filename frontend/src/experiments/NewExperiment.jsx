import React, { useEffect, useRef, useState } from "react";
import { Box, Button, Card, Flex, Grid, Heading, Separator, Text, TextArea, TextField } from "@radix-ui/themes";
import { ChevronDownIcon, ChevronRightIcon } from "@radix-ui/react-icons";
import { fetchJson, postJson } from "../lib.js";
import { ErrorCallout, Notice, PageHeader, icon } from "../ui.jsx";
import { useRouter } from "../router.jsx";
import { RunMarketTemplate } from "../MarketData.jsx";

const DEFAULTS = {
  name: "",
  description: "",
  user_count: 10,
  start_date: "2023-06-15",
  end_date: "2023-06-16",
  max_workers: 1,
  agent_timeout: 300,
  activate_prob: 0.2,
  prob_of_technical: 0.3,
  random_seed: 20260916,
  // undefined：等待读取默认模板；""：内置历史文件；否则为模板 ID 或复制来的 "id@版本"。
  market_template: undefined,
  data_version: null,
};
// 别名可以重名；默认建议“实验 N”，N 取已有实验总数与已用序号中的较大者 + 1。
async function suggestName() {
  const [active, archived] = await Promise.all([
    fetchJson("/api/runs?page_size=100"),
    fetchJson("/api/runs?page_size=100&archived=true"),
  ]);
  const used = [...active.items, ...archived.items]
    .map((run) => /^实验\s*(\d+)$/.exec(run.name.trim())?.[1])
    .filter(Boolean)
    .map(Number);
  return `实验 ${Math.max(active.total + archived.total, ...used) + 1}`;
}

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

function Section({ title, children }) {
  return (
    <Grid columns={{ initial: "1", md: "220px 1fr" }} gap={{ initial: "3", md: "6" }} py="5">
      <Box>
        <Heading as="h2" size="3">
          {title}
        </Heading>
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
          // 旧版快照实验没有模板，复制时改用内置历史文件，由用户重新选择。
          market_template: run.config?.market_template || "",
          data_version: run.config?.market_template ? run.config.data_version : null,
          name: `${run.name} 副本`,
          description: run.description || "",
        }));
      })
      .catch((reason) => setError(`无法读取源实验：${reason.message}`));
  }, [from]);

  useEffect(() => {
    if (from) return;
    suggestName()
      .catch(() => "实验 1")
      // 只填空白的名称，不覆盖用户已经输入的内容。
      .then((name) => setForm((previous) => (previous.name ? previous : { ...previous, name })));
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
  const pickTemplate = (value) => {
    // 首次加载时组件会自动选中默认模板，这不算用户修改。
    if (form.market_template !== undefined && value !== form.market_template && !dirty.current) {
      dirty.current = true;
      setGuard(true);
    }
    setForm((previous) => {
      // 换模板后不再沿用源实验固定的数据版本。
      const keep = previous.market_template === undefined || value === previous.market_template;
      return { ...previous, market_template: value, data_version: keep ? previous.data_version : null };
    });
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
          market_data_mode: config.market_template ? "template" : "legacy",
          market_template: config.market_template || null,
          data_version: config.market_template ? config.data_version : null,
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
      />
      <Card size="3">
        <Section title="基本信息">
          <Field label="实验别名">
            <TextField.Root required value={form.name} placeholder="实验 1" onChange={(e) => change("name", e.target.value)} />
          </Field>
          <Field label="描述（可选）">
            <TextArea value={form.description} onChange={(e) => change("description", e.target.value)} rows={2} placeholder="记录本次实验的假设或变量" />
          </Field>
        </Section>
        <Separator size="4" />
        <Section title="规模与日期">
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
        <Section title="市场环境">
          <RunMarketTemplate
            value={form.market_template}
            onChange={pickTemplate}
            startDate={form.start_date}
            endDate={form.end_date}
            dataVersion={form.data_version}
            onValidity={setMarketValid}
          />
        </Section>
        <Separator size="4" />
        <Section title="高级参数">
          <Box>
            <Button type="button" variant="ghost" color="gray" onClick={() => setAdvanced((v) => !v)} aria-expanded={advanced}>
              {icon(advanced ? ChevronDownIcon : ChevronRightIcon)}
              {advanced ? "收起高级参数" : "展开高级参数"}
            </Button>
          </Box>
          {advanced && (
            <Grid columns={{ initial: "1", sm: "2" }} gap="4">
              <Field label="激活概率">
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
        {!marketValid && form.market_template !== undefined && (
          <Notice>市场环境尚未通过校验，请检查模板的数据覆盖与提示。</Notice>
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
