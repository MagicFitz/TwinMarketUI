import React, { useEffect, useMemo, useState } from "react";
import { Badge, Box, Button, Callout, Code, Flex, Grid, Heading, Select, Separator, Text, TextField } from "@radix-ui/themes";
import { CheckCircledIcon, CrossCircledIcon, LightningBoltIcon } from "@radix-ui/react-icons";
import { fetchJson, postJson, stamp, useData } from "./lib.js";
import { ErrorCallout, icon } from "./ui.jsx";

// 语言模型与向量模型的 OpenAI 兼容接口配置。密钥只写不读：后端只返回“是否已配置”。

const KINDS = [
  ["language", "语言模型", "Agent 决策与发帖使用的 Chat Completions 接口。"],
  ["embedding", "向量模型", "新闻检索使用的 Embeddings 接口。"],
];

const fromSaved = (saved) => ({
  provider: saved.provider === "ollama" ? "ollama" : "openai",
  base_url: saved.base_url || "",
  model_name: saved.model_name || "",
  timeout: saved.timeout ?? "",
  endpoint: saved.endpoint || "",
  max_retries: saved.max_retries ?? "",
  api_key: "",
});

function requestUrl(kind, form) {
  const base = form.base_url.replace(/\/+$/, "");
  if (kind === "language") return base ? `${base}/chat/completions` : "";
  if (form.endpoint) return form.endpoint;
  if (!base) return "";
  return form.provider === "ollama" ? `${base}/api/embed` : `${base}/embeddings`;
}

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

export function StatusOf({ saved }) {
  if (!saved.configured || !saved.model_name) return <Badge color="gray" variant="soft">未配置</Badge>;
  const test = saved.last_test;
  if (!test) return <Badge color="amber" variant="soft">未测试</Badge>;
  return test.ok ? (
    <Badge color="green" variant="soft">可用 · {test.latency_ms} ms</Badge>
  ) : (
    <Badge color="red" variant="soft">连接失败</Badge>
  );
}

function TestResult({ result, unsaved }) {
  if (!result) return null;
  const facts = [
    result.latency_ms != null && `延迟 ${result.latency_ms} ms`,
    result.status_code && `HTTP ${result.status_code}`,
    result.response_model && `模型 ${result.response_model}`,
    result.dimension && `向量维度 ${result.dimension}`,
    !result.used_api_key && "未携带 API Key",
    `测试于 ${stamp(result.tested_at)}`,
  ].filter(Boolean);
  return (
    <Callout.Root color={result.ok ? "green" : "red"} size="1" role="status">
      <Callout.Icon>{icon(result.ok ? CheckCircledIcon : CrossCircledIcon)}</Callout.Icon>
      <Callout.Text>
        <Text weight="medium">{result.ok ? "连接正常" : result.message}</Text>
        <br />
        <Text size="1">{facts.join(" · ")}</Text>
        {unsaved && (
          <>
            <br />
            <Text size="1">本次测试使用的是尚未保存的值。</Text>
          </>
        )}
      </Callout.Text>
    </Callout.Root>
  );
}

function ModelForm({ kind, title, description, saved, onSaved }) {
  const initial = useMemo(() => fromSaved(saved), [saved]);
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [result, setResult] = useState(null);
  const [latencies, setLatencies] = useState([]);
  useEffect(() => setForm(initial), [initial]);

  const change = (key) => (event) => setForm((previous) => ({ ...previous, [key]: event.target.value }));
  const editable = kind === "language" ? ["base_url", "model_name", "timeout"] : ["provider", "base_url", "model_name", "timeout", "endpoint", "max_retries"];
  const changed = editable.filter((key) => String(form[key]) !== String(initial[key]));
  const dirty = changed.length > 0 || form.api_key !== "";
  const numeric = (value) => (value === "" ? null : Number(value));

  // 只把与已保存值不同的字段发去测试；未改动的字段由后端读取已保存配置。
  function overrides() {
    const body = {};
    for (const key of changed) body[key] = ["timeout", "max_retries"].includes(key) ? numeric(form[key]) : form[key];
    if (form.api_key) body.api_key = form.api_key;
    return body;
  }

  async function run(kindOfAction, request) {
    setBusy(kindOfAction);
    setError("");
    setMessage("");
    try {
      await request();
    } catch (reason) {
      setError(reason.message);
    } finally {
      setBusy("");
    }
  }

  const test = () =>
    run("test", async () => {
      const outcome = await postJson(`/api/settings/models/${kind}/test`, overrides());
      setResult({ ...outcome, unsaved: dirty });
      if (outcome.latency_ms != null && outcome.ok) setLatencies((previous) => [...previous, outcome.latency_ms].slice(-5));
      if (!dirty) onSaved();
    });

  const save = (event) => {
    event.preventDefault();
    return run("save", async () => {
      const body = { model_name: form.model_name.trim(), base_url: form.base_url.trim(), timeout: numeric(form.timeout) };
      if (kind === "embedding") Object.assign(body, { provider: form.provider, endpoint: form.endpoint.trim(), max_retries: numeric(form.max_retries) });
      if (form.api_key) body.api_key = form.api_key;
      await fetchJson(`/api/settings/models/${kind}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setForm((previous) => ({ ...previous, api_key: "" }));
      setMessage("已保存。新建的实验在启动时读取这份配置。");
      setResult(null);
      onSaved();
    });
  };

  const clearKey = () =>
    run("clear", async () => {
      await fetchJson(`/api/settings/models/${kind}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_name: initial.model_name, api_key: "" }),
      });
      setMessage("已清除保存的 API Key。");
      onSaved();
    });

  const url = requestUrl(kind, form);
  return (
    <form onSubmit={save} aria-label={`${title}配置`}>
      <Flex justify="between" align="start" gap="3" mb="3" wrap="wrap">
        <Box>
          <Heading as="h3" size="3">
            {title}
          </Heading>
          <Text size="1" color="gray">
            {description}
          </Text>
        </Box>
        <StatusOf saved={saved} />
      </Flex>
      <Grid columns={{ initial: "1", sm: "2" }} gap="3">
        {kind === "embedding" && (
          <Field label="接口类型">
            <Select.Root value={form.provider} onValueChange={(value) => setForm((previous) => ({ ...previous, provider: value }))}>
              <Select.Trigger aria-label={`${title}接口类型`} />
              <Select.Content>
                <Select.Item value="openai">OpenAI 兼容（/embeddings）</Select.Item>
                <Select.Item value="ollama">Ollama（/api/embed）</Select.Item>
              </Select.Content>
            </Select.Root>
          </Field>
        )}
        <Field label="Base URL">
          <TextField.Root required type="url" value={form.base_url} onChange={change("base_url")} placeholder="https://api.openai.com/v1" aria-label={`${title} Base URL`} />
        </Field>
        <Field label="API Key" hint={saved.api_key_configured ? "已配置；留空保持不变。" : "本地服务可留空。"}>
          <TextField.Root
            type="password"
            autoComplete="new-password"
            value={form.api_key}
            onChange={change("api_key")}
            placeholder={saved.api_key_configured ? "••••••••（已配置）" : "sk-…"}
            aria-label={`${title} API Key`}
          />
        </Field>
        <Field label="模型名称">
          <TextField.Root required value={form.model_name} onChange={change("model_name")} placeholder={kind === "language" ? "gpt-4o-mini" : "text-embedding-3-small"} aria-label={`${title}模型名称`} />
        </Field>
        <Field label="超时（秒）">
          <TextField.Root type="number" min="0" value={form.timeout} onChange={change("timeout")} aria-label={`${title}超时`} />
        </Field>
        {kind === "embedding" && (
          <>
            <Field label="最大重试次数">
              <TextField.Root type="number" min="0" value={form.max_retries} onChange={change("max_retries")} aria-label={`${title}最大重试`} />
            </Field>
            <Field label="自定义 Endpoint（可选）" hint="留空时按接口类型由 Base URL 拼接。">
              <TextField.Root type="url" value={form.endpoint} onChange={change("endpoint")} aria-label={`${title}自定义 Endpoint`} />
            </Field>
          </>
        )}
      </Grid>
      <Text as="p" size="1" color="gray" mt="3">
        请求地址：{url ? <Code size="1" variant="ghost">{url}</Code> : "—"}
      </Text>
      <Flex direction="column" gap="2" mt="3">
        <TestResult result={result} unsaved={result?.unsaved} />
        {!result && saved.last_test && !saved.last_test.ok && <TestResult result={saved.last_test} />}
        {latencies.length > 1 && (
          <Text size="1" color="gray">
            最近 {latencies.length} 次延迟：{latencies.join(" · ")} ms（平均 {Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length)} ms）
          </Text>
        )}
        <ErrorCallout>{error}</ErrorCallout>
        {message && (
          <Text size="2" color="green" role="status">
            {message}
          </Text>
        )}
      </Flex>
      <Flex gap="2" mt="3" wrap="wrap" align="center">
        <Button type="submit" disabled={!dirty || !!busy} loading={busy === "save"}>
          保存
        </Button>
        <Button type="button" variant="soft" color="gray" onClick={test} disabled={!form.base_url || !form.model_name || !!busy} loading={busy === "test"}>
          {icon(LightningBoltIcon)} 测试连接
        </Button>
        {dirty && (
          <Button type="button" variant="ghost" color="gray" onClick={() => setForm(initial)} disabled={!!busy}>
            撤销修改
          </Button>
        )}
        <Box flexGrow="1" />
        {saved.api_key_configured && (
          <Button type="button" variant="ghost" color="red" onClick={clearKey} disabled={!!busy} loading={busy === "clear"}>
            清除已保存的 Key
          </Button>
        )}
      </Flex>
    </form>
  );
}

export function ModelSettings() {
  const [revision, setRevision] = useState(0);
  const state = useData("/api/settings/models", revision);
  if (!state.data)
    return (
      <Text size="2" color="gray">
        {state.error || "读取中…"}
      </Text>
    );
  return (
    <Flex direction="column" gap="5">
      {KINDS.map(([kind, title, description], index) => (
        <React.Fragment key={kind}>
          {index > 0 && <Separator size="4" />}
          <ModelForm kind={kind} title={title} description={description} saved={state.data[kind]} onSaved={() => setRevision((v) => v + 1)} />
        </React.Fragment>
      ))}
    </Flex>
  );
}
