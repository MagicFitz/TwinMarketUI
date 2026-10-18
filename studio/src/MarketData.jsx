import React, { useEffect, useState } from 'react';
import { Badge, Box, Button, Card, Checkbox, Code, Dialog, Flex, Heading, Progress, Select, Switch, Table, Text, TextField } from '@radix-ui/themes';
import { Definition, ErrorCallout, Notice, Panel } from './ui.jsx';

export async function marketRequest(path, body, method = 'POST') {
  const response = await fetch(path, body === undefined ? { method } : {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '市场数据请求失败');
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
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '市场数据读取失败');
        if (alive) { setData(result); setError(''); }
      } catch (reason) { if (alive && reason.name !== 'AbortError') setError(reason.message); }
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
  permission_denied: '无权限', rate_limited: '限流', missing: '缺失', ready: '可用', ready_with_warnings: '可用 · 有警告',
  queued: '排队中', running: '同步中', completed: '已完成', cancelled: '已取消',
};
const phases = { queued: '排队', calendar: '交易日历', fetching: '拉取行情', aggregating: '聚合指数', validating: '质量校验',
  publishing: '发布快照', completed: '发布完成', failed: '失败', cancelled: '已取消', rate_limit_wait: '限流等待', retry_wait: '重试等待' };
const datasets = { daily: '日线行情', trade_cal: '交易日历', daily_basic: '每日估值', moneyflow: '资金流', news: '新闻' };
const stamp = value => value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '尚未测试';
const LEGACY = '__legacy__';
const Status = ({ value }) => {
  const color = ['failed', 'permission_denied', 'missing'].includes(value) ? 'amber' : ['untested', 'unconfigured', 'queued', 'cancelled'].includes(value) ? 'gray' : 'green';
  return <Badge color={color} variant="soft" radius="full">{statusText[value] || value}</Badge>;
};

function Capabilities({ entries }) {
  return <Flex gap="2" wrap="wrap" my="3">{entries.map(([key, value, note]) =>
    <Card key={key} size="1" className="capability"><Flex align="center" gap="2"><Text size="2">{datasets[key] || key}</Text><Status value={value} /></Flex>{note && <Text as="p" size="1" color="gray" mt="1">{note}</Text>}</Card>)}</Flex>;
}

export function TushareConnection() {
  const resource = useMarketResource('/api/settings/data-sources');
  const source = resource.data?.items?.[0];
  const [token, setToken] = useState(''), [busy, setBusy] = useState(''), [message, setMessage] = useState(''), [error, setError] = useState('');
  async function act(kind, body) {
    setBusy(kind); setError(''); setMessage('');
    try {
      if (kind === 'test') await marketRequest('/api/settings/data-sources/tushare/test');
      else await marketRequest('/api/settings/data-sources/tushare', body, 'PUT');
      setToken(''); resource.refresh();
      setMessage(kind === 'test' ? '权限测试已完成，请查看各接口状态。' : '数据源设置已保存。');
    } catch (reason) { setError(reason.message); }
    finally { setBusy(''); }
  }
  return <Flex direction="column" gap="3">
    <Flex justify="between" align="center" gap="3" wrap="wrap">
      <Box><Heading as="h3" size="2">Tushare Pro</Heading><Text size="1" color="gray">收盘后日级数据 · 固定官方服务地址</Text></Box>
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

function SnapshotDetails({ snapshot }) {
  return <Flex direction="column" gap="4">
    <Definition values={[['快照', snapshot.snapshot_id], ['覆盖日期', `${snapshot.start_date} 至 ${snapshot.effective_through}`], ['转换规则', snapshot.transform_version], ['最小有效成分权重', `${(snapshot.quality.minimum_weight_coverage * 100).toFixed(2)}%`], ['估值空值', snapshot.quality.valuation_missing_values]]} />
    <Box><Heading as="h3" size="2">数据能力</Heading><Capabilities entries={Object.entries(snapshot.datasets).map(([key, value]) => [key, value.status])} /></Box>
    <Box><Heading as="h3" size="2">质量报告</Heading><ul className="plain-list">{snapshot.quality.warnings.map((warning, index) => <li key={index}><Text size="2">{warning}</Text></li>)}</ul></Box>
    <Box><Heading as="h3" size="2" mb="1">资产定义 SHA-256</Heading><Code size="1" className="break">{snapshot.asset_definition_sha256}</Code></Box>
    <Box><Heading as="h3" size="2" mb="2">文件指纹</Heading>{Object.entries(snapshot.files).map(([name, file]) => <Box key={name} py="2" className="divided"><Text size="2" weight="medium">{name}</Text> <Text size="1" color="gray">{file.bytes.toLocaleString()} bytes</Text><Code as="div" size="1" mt="1" className="break" variant="ghost">{file.sha256}</Code></Box>)}</Box>
  </Flex>;
}

export function MarketWorkbench() {
  const resource = useMarketResource('/api/market/overview', 3000);
  const data = resource.data;
  const [open, setOpen] = useState(false), [detail, setDetail] = useState(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [form, setForm] = useState({ mode: 'incremental', start_date: '', end_date: '', daily_basic: true, allow_missing_news: false });
  const change = (key, value) => setForm(previous => ({ ...previous, [key]: value }));
  const active = data?.active_snapshot;
  const ongoing = data?.jobs.some(job => ['queued', 'running'].includes(job.status));
  function showSync() {
    setError('');
    setForm(previous => ({ ...previous, start_date: data?.suggested_start_date || '', end_date: data?.latest_closed_date || '' }));
    setOpen(true);
  }
  async function sync(event) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      await marketRequest('/api/market/sync-jobs', { ...form, datasets: ['trade_cal', 'daily', ...(form.daily_basic ? ['daily_basic'] : [])] });
      setOpen(false); resource.refresh();
    } catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }
  async function act(path) {
    setBusy(true); setError('');
    try { await marketRequest(path); resource.refresh(); }
    catch (reason) { setError(reason.message); }
    finally { setBusy(false); }
  }
  if (!data) return <Panel title="市场数据工作台"><Flex justify="between" align="center"><Text size="2" color="gray" role={resource.error ? 'alert' : 'status'}>{resource.error || '正在读取市场数据工作台…'}</Text><Button variant="soft" color="gray" onClick={resource.refresh}>刷新</Button></Flex></Panel>;
  return <Flex direction="column" gap="4">
    <Panel title="市场数据工作台" hint="收盘后日级快照 · 本地可复现市场环境。同步完成后手动设为默认，已有实验继续使用创建时固定的快照。"
      actions={<Button disabled={busy || ongoing || !data.source.token_configured || !data.source.enabled} onClick={showSync}>更新数据</Button>}>
      {!data.source.token_configured && <Notice>请先前往 <a href="/studio/Setting#sources">设置 · 金融数据源</a> 配置 Tushare 连接。</Notice>}
      <Definition values={[['新实验默认快照', active?.snapshot_id || '内置历史文件'], ['有效截至', active?.effective_through || data.baseline_through || '未知'], ['质量状态', active ? <Status value={active.status} /> : '尚未发布快照'], ['本地行情已同步至', data.baseline_through]]} />
      {active && <Capabilities entries={Object.entries(active.datasets).map(([key, value]) => [key, value.status])} />}
    </Panel>
    <ErrorCallout>{error || resource.error}</ErrorCallout>
    <Panel title="同步任务" actions={<Button variant="soft" color="gray" size="1" onClick={resource.refresh}>刷新</Button>}>
      {data.jobs.length ? <Flex direction="column" gap="3">{data.jobs.map(job => <Card key={job.job_id} size="1">
        <Flex justify="between" align="center" gap="3"><Box><Text size="2" weight="medium">{phases[job.stage] || job.stage}</Text><Text as="p" size="1" color="gray">{job.request.start_date} 至 {job.request.end_date}</Text></Box><Status value={job.status} /></Flex>
        <Progress my="2" value={job.progress * 100} aria-label={`${job.job_id} 同步进度`} />
        <Flex justify="between" align="center" gap="3"><Text size="1" color="gray">{Math.round(job.progress * 100)}% · {stamp(job.created_at)}</Text>
          {['queued', 'running'].includes(job.status) && <Button size="1" variant="soft" color="red" disabled={busy || job.cancel_requested} onClick={() => act(`/api/market/sync-jobs/${job.job_id}/cancel`)}>{job.cancel_requested ? '等待安全取消' : '取消任务'}</Button>}</Flex>
        {job.error && <Text as="p" size="1" color="red" role="alert">{job.error} · {job.error_code}</Text>}
        {job.snapshot_id && <Text size="1" color="gray">{job.snapshot_id}</Text>}
      </Card>)}</Flex> : <Text size="2" color="gray">暂无同步任务。任务状态保存在本地，刷新页面后可以继续查看。</Text>}
    </Panel>
    <Panel title="不可变环境快照">
      {data.snapshots.length ? <Table.Root size="1"><Table.Header><Table.Row><Table.ColumnHeaderCell>快照</Table.ColumnHeaderCell><Table.ColumnHeaderCell>覆盖范围</Table.ColumnHeaderCell><Table.ColumnHeaderCell>状态</Table.ColumnHeaderCell><Table.ColumnHeaderCell justify="end">操作</Table.ColumnHeaderCell></Table.Row></Table.Header>
        <Table.Body>{data.snapshots.map(snapshot => <Table.Row key={snapshot.snapshot_id} align="center">
          <Table.RowHeaderCell><Text weight="medium">{snapshot.snapshot_id}</Text><Text as="p" size="1" color="gray">{snapshot.active ? '新实验默认' : snapshot.transform_version}</Text></Table.RowHeaderCell>
          <Table.Cell>{snapshot.start_date} → {snapshot.effective_through}</Table.Cell>
          <Table.Cell><Status value={snapshot.status} /></Table.Cell>
          <Table.Cell justify="end"><Flex gap="2" justify="end"><Button size="1" variant="ghost" color="gray" onClick={() => setDetail(snapshot)}>质量与指纹</Button>
            <Button size="1" variant="soft" color="gray" disabled={busy || snapshot.active} onClick={() => act(`/api/market/snapshots/${snapshot.snapshot_id}/activate`)}>{snapshot.active ? '当前默认' : '设为新实验默认'}</Button></Flex></Table.Cell>
        </Table.Row>)}</Table.Body></Table.Root> : <Text size="2" color="gray">首次同步后将在这里展示快照及质量报告。</Text>}
    </Panel>
    <Panel title="资产成分与权重" hint="保留 10 个自定义指数。旧描述中的百分比舍入误差已按比例归一，并记录在资产定义中。">
      <Flex direction="column" gap="2">{Object.entries(data.assets.assets).map(([code, asset]) => <details key={code} className="disclosure"><summary><Text size="2" weight="medium">{code} · {asset.name}</Text> <Text size="1" color="gray">{asset.constituents.length} 只成分股</Text></summary>
        <Table.Root size="1" mt="2"><Table.Header><Table.Row><Table.ColumnHeaderCell>成分股</Table.ColumnHeaderCell><Table.ColumnHeaderCell>代码</Table.ColumnHeaderCell><Table.ColumnHeaderCell justify="end">权重</Table.ColumnHeaderCell></Table.Row></Table.Header>
          <Table.Body>{asset.constituents.map(c => <Table.Row key={c.ts_code}><Table.Cell>{c.name}</Table.Cell><Table.Cell>{c.ts_code}</Table.Cell><Table.Cell justify="end" className="tabular">{(c.weight * 100).toFixed(2)}%</Table.Cell></Table.Row>)}</Table.Body></Table.Root></details>)}</Flex>
    </Panel>
    <Dialog.Root open={open} onOpenChange={value => !busy && setOpen(value)}>
      <Dialog.Content maxWidth="560px">
        <Dialog.Title>同步收盘后行情</Dialog.Title>
        <Dialog.Description size="2" color="gray" mb="4">交易日历、50 只成分股日线必选；每个交易日拉取全市场截面，再生成 10 个自定义指数。</Dialog.Description>
        <form onSubmit={sync}><Flex direction="column" gap="3">
          <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">同步方式</Text><Select.Root value={form.mode} onValueChange={value => change('mode', value)}><Select.Trigger /><Select.Content><Select.Item value="incremental">增量更新 · 重拉最近 3 个交易日</Select.Item><Select.Item value="backfill">指定日期回补</Select.Item></Select.Content></Select.Root></label></Flex>
          {form.mode === 'backfill' && <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">起始日期</Text><TextField.Root type="date" required value={form.start_date} max={form.end_date} onChange={event => change('start_date', event.target.value)} /></label></Flex>}
          <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">结束日期</Text><TextField.Root type="date" required value={form.end_date} max={data.latest_closed_date} onChange={event => change('end_date', event.target.value)} /></label></Flex>
          <Text as="label" size="2"><Flex gap="2" align="center"><Checkbox checked={form.daily_basic} onCheckedChange={value => change('daily_basic', value === true)} />每日估值 · 无权限时保留空值</Flex></Text>
          <Notice>本期未接入新闻和资金流。新闻容器为空，新闻检索停用；这项研究限制会写入 Run 清单。</Notice>
          <Text as="label" size="2"><Flex gap="2" align="center"><Checkbox checked={form.allow_missing_news} onCheckedChange={value => change('allow_missing_news', value === true)} />明确允许无新闻模式</Flex></Text>
          <Text size="1" color="gray">增量起始参考：{data.suggested_start_date}。调用量约为区间交易日数 × {form.daily_basic ? '2' : '1'} 个行情接口，另加交易日历；分页和重试会增加调用量。</Text>
          <ErrorCallout>{error}</ErrorCallout>
          <Flex gap="3" justify="end" mt="2"><Dialog.Close><Button type="button" variant="soft" color="gray" disabled={busy}>取消</Button></Dialog.Close><Button type="submit" loading={busy} disabled={busy || !form.allow_missing_news}>创建同步任务</Button></Flex>
        </Flex></form>
      </Dialog.Content>
    </Dialog.Root>
    <Dialog.Root open={!!detail} onOpenChange={value => !value && setDetail(null)}>
      <Dialog.Content maxWidth="640px"><Dialog.Title>快照质量与文件指纹</Dialog.Title>{detail && <SnapshotDetails snapshot={detail} />}
        <Flex justify="end" mt="4"><Dialog.Close><Button variant="soft" color="gray">关闭</Button></Dialog.Close></Flex></Dialog.Content>
    </Dialog.Root>
  </Flex>;
}

export function RunMarketSnapshot({ value, onChange, startDate, endDate, allowMissingNews, onAllowMissingNews, onValidity }) {
  const resource = useMarketResource('/api/market/snapshots');
  const snapshots = resource.data?.items || [];
  const selected = snapshots.find(item => item.snapshot_id === value);
  useEffect(() => {
    if (value !== undefined || !resource.data) return;
    onChange(snapshots.find(item => item.active)?.snapshot_id || '');
  }, [resource.data, value, onChange]);
  const rangeValid = !value || !!selected && startDate >= selected.earliest_run_date && endDate <= selected.effective_through;
  const newsValid = !selected || selected.datasets.news.status !== 'missing' || allowMissingNews;
  const valid = !!resource.data && value !== undefined && rangeValid && newsValid;
  useEffect(() => { onValidity(valid); }, [valid, onValidity]);
  return <Flex direction="column" gap="2">
    <Flex asChild direction="column" gap="1"><label><Text size="2" weight="medium">市场环境快照</Text>
      {/* Radix Select 不接受空字符串，旧版数据模式用占位值表示。 */}
      <Select.Root value={value === undefined ? undefined : value || LEGACY} onValueChange={next => onChange(next === LEGACY ? '' : next)} disabled={!resource.data}>
        <Select.Trigger placeholder="读取快照…" />
        <Select.Content><Select.Item value={LEGACY}>内置历史文件 · 旧版数据模式</Select.Item>{snapshots.map(item => <Select.Item key={item.snapshot_id} value={item.snapshot_id}>{item.active ? '默认 · ' : ''}{item.snapshot_id}</Select.Item>)}</Select.Content>
      </Select.Root></label></Flex>
    {resource.error && <ErrorCallout>{resource.error} <Button size="1" variant="soft" color="gray" onClick={resource.refresh}>重试</Button></ErrorCallout>}
    {selected && <>
      <Text size="1" color="gray">行情有效截至 {selected.effective_through} · {selected.transform_version} · 资产定义 v{selected.asset_definition_version}</Text>
      <Capabilities entries={Object.entries(selected.datasets).map(([key, data]) => [key, data.status])} />
      {selected.datasets.news?.status === 'missing' && <>
        <Notice>行情已更新，新闻缺失。无新闻环境不会使用旧新闻索引。</Notice>
        <Text as="label" size="2"><Flex gap="2" align="center"><Checkbox checked={allowMissingNews} onCheckedChange={next => onAllowMissingNews(next === true)} />允许此实验使用无新闻环境</Flex></Text>
      </>}
    </>}
    {!rangeValid && <ErrorCallout>快照未覆盖所选日期。<a href="/studio/Market">前往市场更新数据</a>，或调整模拟日期。</ErrorCallout>}
  </Flex>;
}
