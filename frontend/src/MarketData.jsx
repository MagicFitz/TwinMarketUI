import React, { useEffect, useState } from 'react';
import { Badge, Box, Button, Card, Checkbox, Code, Dialog, Flex, Heading, Progress, Select, Switch, Table, Text, TextField } from '@radix-ui/themes';
import { Definition, ErrorCallout, Notice, Panel } from './ui.jsx';

// 市场数据服务（MDS）是独立进程，开发时经 Vite 的 /mds 代理访问。
export const MDS = '/mds/v1';
const OFFLINE = '市场数据服务未连接。请用 script/dev.sh 启动，或运行 python -m market_service。';

export async function marketRequest(path, body, method = 'POST') {
  let response;
  try {
    response = await fetch(path, body === undefined ? { method } : {
      method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  } catch { throw new Error(OFFLINE); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || (response.status >= 500 ? OFFLINE : '市场数据请求失败'));
  return data;
}

function useMarketResource(path, interval = 0) {
  const [data, setData] = useState(null), [error, setError] = useState(''), [revision, refresh] = useState(0);
  useEffect(() => {
    let alive = true, pending = false;
    const controller = new AbortController();
    async function read() {
      if (pending) return;
      pending = true;
      try {
        const response = await fetch(path, { signal: controller.signal });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.error || (response.status >= 500 ? OFFLINE : '市场数据读取失败'));
        if (alive) { setData(result); setError(''); }
      } catch (reason) { if (alive && reason.name !== 'AbortError') setError(reason.message === 'Failed to fetch' ? OFFLINE : reason.message); }
      finally { pending = false; }
    }
    read();
    const timer = interval ? setInterval(read, interval) : null;
    return () => { alive = false; controller.abort(); clearInterval(timer); };
  }, [path, interval, revision]);
  return { data, error, refresh: () => refresh(value => value + 1) };
}

const statusText = {
  unconfigured: '未配置', untested: '尚未测试', available: '可用', partial: '部分权限', failed: '失败',
  permission_denied: '无权限', rate_limited: '限流', missing: '缺失',
  queued: '排队中', running: '同步中', completed: '已完成', cancelled: '已取消',
  ok: '满足', warn: '提示', block: '不满足', disabled: '未启用',
};
const phases = { queued: '排队', calendar: '交易日历', fetching: '拉取行情', aggregating: '聚合指数', validating: '质量校验',
  publishing: '写入数据版本', completed: '已生成新版本', unchanged: '数据无变化', failed: '失败', cancelled: '已取消',
  rate_limit_wait: '限流等待', retry_wait: '重试等待' };
const datasets = { daily: '日线行情', trade_cal: '交易日历', daily_basic: '每日估值', moneyflow: '资金流', news: '新闻' };
const kinds = { seed: '种子导入', market_daily: '行情同步' };
const stamp = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '尚未测试';
const LEGACY = '__legacy__';
const Status = ({ value }) => {
  const color = ['failed', 'permission_denied', 'missing', 'warn'].includes(value) ? 'amber' : value === 'block' ? 'red'
    : ['untested', 'unconfigured', 'queued', 'cancelled', 'disabled'].includes(value) ? 'gray' : 'green';
  return <Badge color={color} variant="soft" radius="full">{statusText[value] || value}</Badge>;
};

function Capabilities({ entries }) {
  return <Flex gap="2" wrap="wrap" my="3">{entries.map(([key, value, note]) =>
    <Card key={key} size="1" className="capability"><Flex align="center" gap="2"><Text size="2">{datasets[key] || key}</Text><Status value={value} /></Flex>{note && <Text as="p" size="1" color="gray" mt="1">{note}</Text>}</Card>)}</Flex>;
}

export function TushareConnection() {
  const resource = useMarketResource(`${MDS}/sources/tushare`);
  const source = resource.data;
  const [token, setToken] = useState(''), [busy, setBusy] = useState(''), [message, setMessage] = useState(''), [error, setError] = useState('');
  async function act(kind, body) {
    setBusy(kind); setError(''); setMessage('');
    try {
      if (kind === 'test') await marketRequest(`${MDS}/sources/tushare/test`);
      else await marketRequest(`${MDS}/sources/tushare`, body, 'PUT');
      setToken(''); resource.refresh();
      setMessage(kind === 'test' ? '权限测试已完成，请查看各接口状态。' : '数据源设置已保存。');
    } catch (reason) { setError(reason.message); }
    finally { setBusy(''); }
  }
  return <Flex direction="column" gap="3">
    <Flex justify="between" align="center" gap="3" wrap="wrap">
      <Box><Heading as="h3" size="2">Tushare Pro</Heading><Text size="1" color="gray">由市场数据服务保管 · 固定官方服务地址</Text></Box>
      <Status value={source?.status || 'unconfigured'} />
    </Flex>
    {source && <Flex justify="between" align="center" gap="3"><Text size="2" color="gray">{source.token_configured ? `Token 已配置 · ${source.credential_source === 'environment' ? '环境变量优先' : '本地保密配置'}` : '尚未配置 Token'}</Text>
      <Text as="label" size="2"><Flex gap="2" align="center"><Switch checked={source.enabled} disabled={!!busy} onCheckedChange={enabled => act('save', { enabled })} />{source.enabled ? '已启用' : '已停用'}</Flex></Text></Flex>}
    <form onSubmit={event => { event.preventDefault(); act('save', token.trim() ? { token } : {}); }}>
      <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">新增或替换 Token</Text>
        <TextField.Root type="password" autoComplete="new-password" value={token} onChange={event => setToken(event.target.value)} placeholder="留空保留现有 Token" disabled={!!busy || !source} /></label></Flex>
      <Flex gap="2" wrap="wrap" mt="3">
        <Button type="submit" disabled={!!busy || !source} loading={busy === 'save'}>保存连接</Button>
        <Button type="button" variant="soft" color="gray" onClick={() => act('test')} disabled={!!busy || !source?.token_configured || !source?.enabled} loading={busy === 'test'}>测试连接</Button>
        <Button type="button" variant="ghost" color="red" disabled={!!busy || !source?.token_configured || source?.credential_source === 'environment'} onClick={() => act('clear', { token: '' })}>清除本地 Token</Button>
      </Flex>
    </form>
    <Capabilities entries={Object.keys(datasets).filter(key => ['daily', 'trade_cal', 'daily_basic'].includes(key)).map(key => [key, source?.capabilities?.[key]?.status || 'untested', source?.capabilities?.[key]?.message])} />
    <Text size="1" color="gray">最近测试：{stamp(source?.last_tested_at)}。新闻、资金流和盘中预览将在后续接入。</Text>
    <ErrorCallout>{error || resource.error}</ErrorCallout>
    {message && <Text size="2" color="green" role="status">{message}</Text>}
  </Flex>;
}

function VersionDetails({ version }) {
  const summary = version.summary || {};
  const changes = Object.entries(summary.changes || {}).map(([table, value]) => `${table}：新增 ${value.inserted}，修订 ${value.revised}`);
  return <Flex direction="column" gap="4">
    <Definition values={[['数据版本', version.data_version], ['类型', kinds[version.kind] || version.kind], ['创建时间', stamp(version.created_at)],
      ['行情有效至', summary.effective_through || '—'], ['来源', summary.source || '—'], ['同步任务', version.job_id || '—']]} />
    {summary.datasets && <Box><Heading as="h3" size="2">数据能力</Heading><Capabilities entries={Object.entries(summary.datasets)} /></Box>}
    <Box><Heading as="h3" size="2">变更</Heading><ul className="plain-list">{changes.map(item => <li key={item}><Text size="2">{item}</Text></li>)}</ul></Box>
    <Box><Heading as="h3" size="2">质量提示</Heading>{summary.warnings?.length ? <ul className="plain-list">{summary.warnings.map((warning, index) => <li key={index}><Text size="2">{warning}</Text></li>)}</ul> : <Text size="2" color="gray">无</Text>}</Box>
    {summary.asset_definition_sha256 && <Box><Heading as="h3" size="2" mb="1">资产定义 SHA-256</Heading><Code size="1" className="break">{summary.asset_definition_sha256}</Code></Box>}
  </Flex>;
}

function TemplateDetails({ template }) {
  const { session, rules, news } = template;
  return <Flex direction="column" gap="4">
    <Text size="2" color="gray">{template.description}</Text>
    <Definition values={[['模板版本', template.ref], ['信息截止时刻', `${session.information_cutoff}（${session.timezone}）`], ['撮合方式', session.matching],
      ['涨跌停', `±${rules.price_limit_pct}%`], ['最小交易单位', `${rules.lot_size} 股`], ['非交易日', session.non_trading_day.forum ? '仅论坛活动' : '不活动'],
      ['新闻', news.enabled ? `${news.sources.join('、')} · 每日 ${news.top_k ?? '全部'} 条` : '未启用']]} />
    <Box><Heading as="h3" size="2" mb="1">模板 SHA-256</Heading><Code size="1" className="break">{template.sha256}</Code></Box>
    <Box><Heading as="h3" size="2" mb="1">完整定义</Heading><pre className="code-block">{JSON.stringify(template.body, null, 2)}</pre></Box>
  </Flex>;
}

export function MarketWorkbench() {
  const resource = useMarketResource(`${MDS}/overview`, 3000);
  const data = resource.data;
  const [open, setOpen] = useState(false), [detail, setDetail] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [form, setForm] = useState({ mode: 'incremental', start_date: '', end_date: '', daily_basic: true });
  const change = (key, value) => setForm(previous => ({ ...previous, [key]: value }));
  const ongoing = data?.jobs.some(job => ['queued', 'running'].includes(job.status));
  function showSync() {
    setError('');
    setForm(previous => ({ ...previous, start_date: data?.suggested_start_date || '', end_date: data?.latest_closed_date || '' }));
    setOpen(true);
  }
  async function sync(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const { daily_basic, ...request } = form;
      await marketRequest(`${MDS}/jobs`, { ...request, datasets: ['trade_cal', 'daily', ...(daily_basic ? ['daily_basic'] : [])] });
      setOpen(false); resource.refresh();
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }
  async function act(path, body, method) {
    setBusy(true); setError('');
    try { await marketRequest(path, body, method); resource.refresh(); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }
  if (!data) return <Panel title="市场数据服务"><Flex justify="between" align="center"><Text size="2" color="gray" role={resource.error ? 'alert' : 'status'}>{resource.error || '正在读取市场数据服务…'}</Text><Button variant="soft" color="gray" onClick={resource.refresh}>刷新</Button></Flex></Panel>;
  const news = Object.entries(data.datasets.news || {});
  return <Flex direction="column" gap="4">
    <Panel title="市场数据服务"
      actions={<Button disabled={busy || ongoing || !data.source.token_configured || !data.source.enabled} onClick={showSync}>更新数据</Button>}>
      {!data.source.token_configured && <Notice>请先前往 <a href="/studio/Setting#sources">设置 · 金融数据源</a> 配置 Tushare 连接。</Notice>}
      <Definition values={[['最新数据版本', data.data_version ?? '—'], ['行情已同步至', data.baseline_through || '—'], ['交易日历至', data.datasets.calendar?.through || '—'], ['最近可同步收盘日', data.latest_closed_date]]} />
      <Flex gap="2" wrap="wrap" my="3">
        <Card size="1" className="capability"><Text size="2">指数日线</Text><Text as="p" size="1" color="gray" mt="1">{data.datasets.index_daily?.from} → {data.datasets.index_daily?.through} · {data.datasets.index_daily?.rows?.toLocaleString()} 行</Text></Card>
        {news.length ? news.map(([source, item]) => <Card key={source} size="1" className="capability"><Text size="2">新闻 · {source}</Text><Text as="p" size="1" color="gray" mt="1">{item.from} → {item.through} · {item.rows.toLocaleString()} 条</Text></Card>)
          : <Card size="1" className="capability"><Flex align="center" gap="2"><Text size="2">新闻</Text><Status value="missing" /></Flex></Card>}
      </Flex>
    </Panel>
    <ErrorCallout>{error || resource.error}</ErrorCallout>
    <Panel title="市场模板">
      <Table.Root size="1"><Table.Header><Table.Row><Table.ColumnHeaderCell>模板</Table.ColumnHeaderCell><Table.ColumnHeaderCell>新闻</Table.ColumnHeaderCell><Table.ColumnHeaderCell>规则</Table.ColumnHeaderCell><Table.ColumnHeaderCell justify="end">操作</Table.ColumnHeaderCell></Table.Row></Table.Header>
        <Table.Body>{data.templates.map(template => <Table.Row key={template.template_id} align="center">
          <Table.RowHeaderCell><Text weight="medium">{template.name}</Text><Text as="p" size="1" color="gray">{template.default ? '新实验默认 · ' : ''}{template.ref}</Text></Table.RowHeaderCell>
          <Table.Cell>{template.news.enabled ? `${template.news.sources.length} 个来源 · 每日 ${template.news.top_k ?? '全部'} 条` : '未启用'}</Table.Cell>
          <Table.Cell>截止 {template.session.information_cutoff} · 涨跌停 ±{template.rules.price_limit_pct}%</Table.Cell>
          <Table.Cell justify="end"><Flex gap="2" justify="end"><Button size="1" variant="ghost" color="gray" onClick={() => setDetail({ template })}>查看定义</Button>
            <Button size="1" variant="soft" color="gray" disabled={busy || template.default} onClick={() => act(`${MDS}/templates/default`, { template_id: template.template_id }, 'PUT')}>{template.default ? '当前默认' : '设为默认'}</Button></Flex></Table.Cell>
        </Table.Row>)}</Table.Body></Table.Root>
    </Panel>
    <Panel title="同步任务" actions={<Button variant="soft" color="gray" size="1" onClick={resource.refresh}>刷新</Button>}>
      {data.jobs.length ? <Flex direction="column" gap="3">{data.jobs.map(job => <Card key={job.job_id} size="1">
        <Flex justify="between" align="center" gap="3"><Box><Text size="2" weight="medium">{phases[job.stage] || job.stage}</Text><Text as="p" size="1" color="gray">{job.request.start_date} 至 {job.request.end_date}</Text></Box><Status value={job.status} /></Flex>
        <Progress my="2" value={job.progress * 100} aria-label={`${job.job_id} 同步进度`} />
        <Flex justify="between" align="center" gap="3"><Text size="1" color="gray">{Math.round(job.progress * 100)}% · {stamp(job.created_at)}</Text>
          {['queued', 'running'].includes(job.status) && <Button size="1" variant="soft" color="red" disabled={busy || job.cancel_requested} onClick={() => act(`${MDS}/jobs/${job.job_id}/cancel`)}>{job.cancel_requested ? '等待安全取消' : '取消任务'}</Button>}</Flex>
        {job.error && <Text as="p" size="1" color="red" role="alert">{job.error} · {job.error_code}</Text>}
        {job.data_version && <Text size="1" color="gray">数据版本 {job.data_version}</Text>}
      </Card>)}</Flex> : <Text size="2" color="gray">暂无同步任务。任务状态保存在市场数据服务中，刷新页面后可以继续查看。</Text>}
    </Panel>
    <Panel title="数据版本">
      <Table.Root size="1"><Table.Header><Table.Row><Table.ColumnHeaderCell>版本</Table.ColumnHeaderCell><Table.ColumnHeaderCell>类型</Table.ColumnHeaderCell><Table.ColumnHeaderCell>行情有效至</Table.ColumnHeaderCell><Table.ColumnHeaderCell>提示</Table.ColumnHeaderCell><Table.ColumnHeaderCell justify="end">操作</Table.ColumnHeaderCell></Table.Row></Table.Header>
        <Table.Body>{data.versions.map(version => <Table.Row key={version.data_version} align="center">
          <Table.RowHeaderCell><Text weight="medium">v{version.data_version}</Text><Text as="p" size="1" color="gray">{stamp(version.created_at)}</Text></Table.RowHeaderCell>
          <Table.Cell>{kinds[version.kind] || version.kind}</Table.Cell>
          <Table.Cell>{version.summary.effective_through || '—'}</Table.Cell>
          <Table.Cell>{version.summary.warnings?.length ? <Status value="warn" /> : <Status value="ok" />}</Table.Cell>
          <Table.Cell justify="end"><Button size="1" variant="ghost" color="gray" onClick={() => setDetail({ version })}>详情</Button></Table.Cell>
        </Table.Row>)}</Table.Body></Table.Root>
    </Panel>
    <Panel title="资产成分与权重">
      <Flex direction="column" gap="2">{Object.entries(data.assets.assets).map(([code, asset]) => <details key={code} className="disclosure"><summary><Text size="2" weight="medium">{code} · {asset.name}</Text> <Text size="1" color="gray">{asset.constituents.length} 只成分股</Text></summary>
        <Table.Root size="1" mt="2"><Table.Header><Table.Row><Table.ColumnHeaderCell>成分股</Table.ColumnHeaderCell><Table.ColumnHeaderCell>代码</Table.ColumnHeaderCell><Table.ColumnHeaderCell justify="end">权重</Table.ColumnHeaderCell></Table.Row></Table.Header>
          <Table.Body>{asset.constituents.map(c => <Table.Row key={c.ts_code}><Table.Cell>{c.name}</Table.Cell><Table.Cell>{c.ts_code}</Table.Cell><Table.Cell justify="end" className="tabular">{(c.weight * 100).toFixed(2)}%</Table.Cell></Table.Row>)}</Table.Body></Table.Root></details>)}</Flex>
    </Panel>
    <Dialog.Root open={open} onOpenChange={value => !busy && setOpen(value)}>
      <Dialog.Content maxWidth="560px">
        <Dialog.Title>同步收盘后行情</Dialog.Title>
        <Dialog.Description size="2" color="gray" mb="4">交易日历、50 只成分股日线必选；每个交易日拉取全市场截面，再生成 10 个自定义指数，成功后写入一个新的数据版本。</Dialog.Description>
        <form onSubmit={sync}><Flex direction="column" gap="3">
          <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">同步方式</Text><Select.Root value={form.mode} onValueChange={value => change('mode', value)}><Select.Trigger /><Select.Content><Select.Item value="incremental">增量更新 · 重拉最近 3 个交易日</Select.Item><Select.Item value="backfill">指定日期回补</Select.Item></Select.Content></Select.Root></label></Flex>
          {form.mode === 'backfill' && <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">起始日期</Text><TextField.Root type="date" required value={form.start_date} max={form.end_date} onChange={event => change('start_date', event.target.value)} /></label></Flex>}
          <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">结束日期</Text><TextField.Root type="date" required value={form.end_date} min={data.baseline_through || undefined} max={data.latest_closed_date} onChange={event => change('end_date', event.target.value)} /></label></Flex>
          <Text as="label" size="2"><Flex gap="2" align="center"><Checkbox checked={form.daily_basic} onCheckedChange={value => change('daily_basic', value === true)} />每日估值 · 无权限时保留空值</Flex></Text>
          <Text size="1" color="gray">增量起始参考：{data.suggested_start_date}。调用量约为区间交易日数 × {form.daily_basic ? '2' : '1'} 个行情接口，另加交易日历；分页和重试会增加调用量。</Text>
          <ErrorCallout>{error}</ErrorCallout>
          <Flex gap="3" justify="end" mt="2"><Dialog.Close><Button type="button" variant="soft" color="gray" disabled={busy}>取消</Button></Dialog.Close><Button type="submit" loading={busy} disabled={busy}>创建同步任务</Button></Flex>
        </Flex></form>
      </Dialog.Content>
    </Dialog.Root>
    <Dialog.Root open={!!detail} onOpenChange={value => !value && setDetail(null)}>
      <Dialog.Content maxWidth="640px"><Dialog.Title>{detail?.template ? detail.template.name : '数据版本详情'}</Dialog.Title>
        {detail?.version && <VersionDetails version={detail.version} />}
        {detail?.template && <TemplateDetails template={detail.template} />}
        <Flex justify="end" mt="4"><Dialog.Close><Button variant="soft" color="gray">关闭</Button></Dialog.Close></Flex></Dialog.Content>
    </Dialog.Root>
  </Flex>;
}

// 选择市场模板并实时检查所选日期的数据是否满足模板要求。value 为模板 ID（创建时固定最新版本）
// 或复制实验时带来的 "id@版本"；空字符串表示内置历史文件。
export function RunMarketTemplate({ value, onChange, startDate, endDate, dataVersion, onValidity }) {
  const resource = useMarketResource(`${MDS}/templates`);
  const templates = resource.data?.items || [];
  const [plan, setPlan] = useState(null), [planError, setPlanError] = useState(''), [acknowledged, setAcknowledged] = useState(false);
  useEffect(() => {
    if (value !== undefined || !resource.data) return;
    onChange(resource.data.default || '');
  }, [resource.data, value, onChange]);
  // 服务离线时退回内置历史文件，避免表单无法提交。
  useEffect(() => { if (value === undefined && resource.error) onChange(''); }, [resource.error, value, onChange]);
  useEffect(() => {
    setPlan(null); setPlanError(''); setAcknowledged(false);
    if (!value || !startDate || !endDate || startDate > endDate) return;
    const timer = setTimeout(() => marketRequest(`${MDS}/plans`, { template: value, start_date: startDate, end_date: endDate, data_version: dataVersion ?? null })
      .then(setPlan, reason => setPlanError(reason.message)), 300);
    return () => clearTimeout(timer);
  }, [value, startDate, endDate, dataVersion]);
  const warnings = plan?.checks.filter(item => item.status === 'warn') || [];
  const valid = value === '' || (!!plan && plan.ok && (!warnings.length || acknowledged));
  useEffect(() => { onValidity(valid); }, [valid, onValidity]);
  const ids = templates.map(item => item.template_id);
  const selected = templates.find(item => item.template_id === value?.split('@')[0]);
  return <Flex direction="column" gap="3">
    <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">市场模板</Text>
      {/* Radix Select 不接受空字符串，旧版数据模式用占位值表示。 */}
      <Select.Root value={value === undefined ? undefined : value || LEGACY} onValueChange={next => onChange(next === LEGACY ? '' : next)} disabled={value === undefined}>
        <Select.Trigger placeholder="读取模板…" />
        <Select.Content>
          {templates.map(item => <Select.Item key={item.template_id} value={item.template_id}>{item.default ? '默认 · ' : ''}{item.name}（{item.ref}）</Select.Item>)}
          {value && !ids.includes(value) && <Select.Item value={value}>{value} · 源实验固定的版本</Select.Item>}
          <Select.Item value={LEGACY}>内置历史文件 · 旧版数据模式</Select.Item>
        </Select.Content>
      </Select.Root></label></Flex>
    {resource.error && <ErrorCallout>{resource.error} <Button size="1" variant="soft" color="gray" onClick={resource.refresh}>重试</Button></ErrorCallout>}
    {selected && <Text size="1" color="gray">{selected.description} 信息截止 {selected.session.information_cutoff} · 涨跌停 ±{selected.rules.price_limit_pct}% · 新闻{selected.news.enabled ? `每日 ${selected.news.top_k ?? '全部'} 条` : '关闭'}{dataVersion ? ` · 固定数据版本 v${dataVersion}` : ''}</Text>}
    {value && !plan && !planError && <Text size="1" color="gray" role="status">正在检查数据覆盖…</Text>}
    {planError && <ErrorCallout>{planError}</ErrorCallout>}
    {plan && <Card size="1"><Flex direction="column" gap="2">
      <Text size="1" color="gray">数据版本 v{plan.data_version} · {plan.template.ref}</Text>
      {plan.checks.map(item => <Flex key={item.item} gap="2" align="start"><Box flexShrink="0"><Status value={item.status} /></Box><Text size="2"><Text weight="medium">{item.label}</Text>：{item.message}</Text></Flex>)}
      {!plan.ok && <Text size="1" color="red">数据不满足模板要求。<a href="/studio/Market">前往市场更新数据</a>，或调整模拟日期。</Text>}
      {plan.ok && warnings.length > 0 && <Text as="label" size="2"><Flex gap="2" align="center"><Checkbox checked={acknowledged} onCheckedChange={next => setAcknowledged(next === true)} />我已知悉以上提示，仍使用此环境</Flex></Text>}
    </Flex></Card>}
  </Flex>;
}
