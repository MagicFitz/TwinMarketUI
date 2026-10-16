import React, { useEffect, useMemo, useState } from 'react';
import { Button, Card, Drawer, Tag } from 'animal-island-ui';
import { MagnifyingGlassIcon } from '@radix-ui/react-icons';
import './runtime.css';
import { LLMContextButton } from './LLMContextInspector.jsx';

const DAY = [['prepare','Environment'], ['agents','Agent Processing'], ['market','Market'], ['social','Social'], ['commit','Commit']];
const PIPELINE = [['state','State'], ['perception','Perception'], ['interpretation','Interpretation'], ['decision','Decision'], ['action','Action'], ['commit','Commit']];
const label = {draft:'草稿',queued:'排队中',running:'运行中',stopping:'正在停止',completed:'已完成',failed:'失败',cancelled:'已取消',pending:'待处理',retrying:'重试中',skipped:'已跳过'};
const fetchJson = async (url, init) => { const response = await fetch(url, init); if (!response.ok) throw new Error((await response.json().catch(()=>({}))).error || '请求失败'); return response.json(); };
const StateTag = ({value}) => <Tag size="small" color={value==='failed'?'red':value==='running'||value==='retrying'?'orange':value==='completed'?'green':'gray'} variant="soft">{label[value] || value || '—'}</Tag>;
const elapsed = value => value == null ? '—' : `${Math.max(0, Math.round(value / 1000))} 秒`;
const activeRun = value => ['queued','running','stopping'].includes(value);

function currentStage(day) {
  const entries = Object.entries(day?.stages || {});
  const running = entries.find(([, value]) => value?.status === 'running');
  if (running) return running[1].substage || running[1].message || running[0];
  const pending = entries.find(([, value]) => ['pending', 'waiting'].includes(value?.status));
  return pending ? pending[0] : day?.date ? '当日已完成' : '等待启动';
}

function RuntimeLog({runId, status}) {
  const [logs, setLogs] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const read = async () => {
      try {
        const data = await fetchJson(`/api/runs/${runId}/logs?page=1&page_size=100`);
        if (alive) { setLogs(data.items || []); setError(''); }
      } catch (reason) { if (alive) setError(reason.message); }
    };
    read();
    const timer = activeRun(status) ? setInterval(read, 1000) : null;
    return () => { alive = false; if (timer) clearInterval(timer); };
  }, [runId, status]);
  return <section className="runtime-log" aria-label="实时运行日志">
    <div className="runtime-title"><div><h2>实时运行日志</h2><small>仅显示经过后端脱敏的运行进度；运行期间每秒刷新。</small></div><span className={`terminal-status ${activeRun(status) ? 'is-live' : ''}`}>{activeRun(status) ? 'LIVE' : 'ARCHIVE'}</span></div>
    <div className="terminal" role="log" aria-live="polite" aria-relevant="additions text">
      <div className="terminal-line terminal-command">$ twinmarket simulation --run {runId}</div>
      {logs.length ? logs.map((item, index) => <div className="terminal-line" key={`${index}-${item}`}><span className="terminal-prompt">›</span>{item}</div>) : <div className="terminal-line terminal-muted"><span className="terminal-prompt">›</span>{activeRun(status) ? '等待安全运行日志写入…' : '当前没有可显示的运行日志。'}</div>}
      {error && <div className="terminal-line terminal-error"><span className="terminal-prompt">!</span>日志读取失败：{error}</div>}
    </div>
  </section>;
}

export function RunComposer({onCreated}) {
  const [form, setForm] = useState({name:'10 Agent 观察运行', user_count:10, start_date:'2023-06-15', end_date:'2023-06-16', max_workers:1, agent_timeout:300, activate_prob:0.2, prob_of_technical:0.3, random_seed:20260916});
  const [advanced,setAdvanced] = useState(false), [error,setError] = useState(''), [saving,setSaving] = useState(false);
  const change = (key, value) => setForm(previous => ({...previous,[key]:value}));
  async function submit(event) {
    event.preventDefault(); setSaving(true); setError('');
    try { const run = await fetchJson('/api/runs', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:form.name, config:form})}); onCreated(run.run_id); }
    catch (reason) { setError(reason.message); } finally { setSaving(false); }
  }
  return <form className="run-composer" onSubmit={submit}><p>创建后会冻结非敏感参数与输入文件指纹；创建成功将直接进入运行台，再由你明确点击“运行模拟”。</p>
    <label>实例名称<input required value={form.name} onChange={e=>change('name',e.target.value)}/></label>
    <div className="form-grid"><label>用户数量<input required type="number" min="1" max="1000" value={form.user_count} onChange={e=>change('user_count',Number(e.target.value))}/></label><label>并发 Worker<input required type="number" min="1" value={form.max_workers} onChange={e=>change('max_workers',Number(e.target.value))}/></label><label>起始日期<input required type="date" value={form.start_date} onChange={e=>change('start_date',e.target.value)}/></label><label>结束日期<input required min={form.start_date} type="date" value={form.end_date} onChange={e=>change('end_date',e.target.value)}/></label></div>
    <Button type="text" onClick={()=>setAdvanced(v=>!v)} aria-expanded={advanced}>高级参数 {advanced?'收起':'展开'}</Button>
    {advanced && <div className="form-grid"><label>激活概率<input type="number" min="0" max="1" step="0.05" value={form.activate_prob} onChange={e=>change('activate_prob',Number(e.target.value))}/></label><label>技术面概率<input type="number" min="0" max="1" step="0.05" value={form.prob_of_technical} onChange={e=>change('prob_of_technical',Number(e.target.value))}/></label><label>Agent 超时（秒）<input type="number" min="30" max="3600" value={form.agent_timeout} onChange={e=>change('agent_timeout',Number(e.target.value))}/></label><label>随机种子<input type="number" value={form.random_seed} onChange={e=>change('random_seed',Number(e.target.value))}/></label></div>}
    {error && <p className="runtime-error" role="alert">{error}</p>}<Button htmlType="submit" type="primary" loading={saving}>创建实例</Button>
  </form>;
}

function DayCycle({day}) { return <Card className="runtime-panel"><h2>Day Cycle <small>{day.date || '尚未开始'}</small></h2><div className="day-cycle">{DAY.map(([id,name])=>{const stage=day.stages?.[id] || {}; return <article key={id} className={`day-slot ${stage.status || 'pending'}`}><strong>{name}</strong><StateTag value={stage.status}/><small>{stage.substage || stage.message || '等待事件'}</small></article>;})}</div></Card>; }
const STAGE_NAME = {state: '读取状态', perception: '感知信息', interpretation: '形成判断（LLM）', decision: '交易意图', action: '可执行动作', commit: '提交产物'};
const stageName = stage => STAGE_NAME[stage] || stage || '—';
const seconds = ms => ms == null ? '—' : ms < 60000 ? `${Math.round(ms / 1000)} 秒` : `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`;
const sinceMs = (iso, now) => iso ? Math.max(0, now - Date.parse(iso)) : null;
const STATUS_ORDER = {failed: 0, retrying: 1, running: 2, pending: 3, completed: 4, skipped: 5};

// 每秒刷新一次，让运行中阶段的耗时实时变化。
function useNow(active) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

function AgentError({error, previous}) {
  if (!error) return null;
  return <div className="agent-error" role="alert">
    <strong>{previous ? '上次失败于「' : '失败于「'}{stageName(error.stage)}」{error.substage ? ` · ${error.substage}` : ''} · 第 {error.attempt || 1} 次尝试</strong>
    <code>{error.code || 'Error'}</code>
    <p>{error.summary || error.message || '没有错误详情'}</p>
    {error.at && <small>{new Date(error.at).toLocaleTimeString()}</small>}
  </div>;
}

function Pipeline({agent, now, runId, live}) {
  return <Card className="runtime-panel">
    <div className="runtime-title">
      <h2>Agent {agent?.agent_id || '—'} {agent?.attempt > 1 && <small>第 {agent.attempt} 次尝试</small>}</h2>
      <div className="runtime-actions"><LLMContextButton runId={runId} agent={agent} live={live}/><StateTag value={agent?.status}/></div>
    </div>
    {!agent ? <p className="runtime-empty">等待 Agent 事件…</p> : <>
      <AgentError error={agent.error} previous={agent.status !== 'failed'}/>
      <div className="agent-pipeline">{PIPELINE.map(([id]) => {
        const stage = agent.stages?.[id] || {};
        const live = ['running', 'retrying'].includes(stage.status);
        return <article key={id} className={`pipeline-slot ${stage.status || 'pending'}`} tabIndex="0">
          <div><strong>{stageName(id)}</strong><StateTag value={stage.status}/></div>
          <small>{stage.status === 'failed' ? (stage.error_summary || stage.message) : (stage.message || '等待')}</small>
          <span>{live ? `已进行 ${seconds(sinceMs(stage.started_at, now))}` : seconds(stage.duration_ms)}{stage.substage ? ` · ${stage.substage}` : ''}</span>
        </article>;
      })}</div>
    </>}
  </Card>;
}

const FILTERS = [['all', '全部'], ['problem', '失败/重试'], ['running', '运行中'], ['completed', '已完成']];

function AgentBoard({agents, focusId, onFocus, timeout, now}) {
  const [filter, setFilter] = useState('all');
  const counts = agents.reduce((acc, agent) => ({...acc, [agent.status || 'pending']: (acc[agent.status || 'pending'] || 0) + 1}), {});
  const isProblem = agent => ['failed', 'retrying'].includes(agent.status) || Boolean(agent.error);
  const matches = {all: () => true, problem: isProblem, running: agent => agent.status === 'running', completed: agent => agent.status === 'completed'};
  const visible = agents
    .filter(matches[filter])
    .sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
      || String(a.stage_started_at || '').localeCompare(String(b.stage_started_at || '')));
  const total = agents.length || 1;
  return <Card className="runtime-panel">
    <div className="runtime-title"><h2>Agent 状态 <small>超时阈值 {timeout} 秒</small></h2></div>
    <div className="agent-progress" aria-label="Agent 完成进度">
      {['completed', 'running', 'retrying', 'failed'].map(key => <i key={key} className={key} style={{width: `${100 * (counts[key] || 0) / total}%`}}/>)}
    </div>
    <div className="agent-filters">{FILTERS.map(([id, name]) => <Button key={id} size="small" type={filter === id ? 'primary' : 'text'} onClick={() => setFilter(id)}>
      {name} {agents.filter(matches[id]).length}
    </Button>)}</div>
    <div className="agent-rows">{visible.length ? visible.map(agent => {
      const running = ['running', 'retrying'].includes(agent.status);
      const age = running ? sinceMs(agent.stage_started_at, now) : null;
      const slow = age != null && age > timeout * 800;
      const stageNow = agent.stages?.[agent.current_stage] || {};
      const problem = agent.error || (agent.status === 'retrying' ? {code: stageNow.error_code, summary: stageNow.error_summary, message: stageNow.message} : null);
      return <button key={agent.agent_id} className={`agent-row ${agent.agent_id === focusId ? 'is-focus' : ''} ${slow ? 'is-slow' : ''}`} onClick={() => onFocus(agent.agent_id)}>
        <b>{agent.agent_id}</b>
        <StateTag value={agent.status}/>
        <span>{agent.status === 'completed' ? '' : stageName(agent.status === 'failed' ? agent.error?.stage : agent.current_stage)}{agent.attempt > 1 ? ` · 第 ${agent.attempt} 次` : ''}</span>
        <span className="agent-age">{running ? seconds(age) : ''}{slow ? ' · 接近超时' : ''}</span>
        {problem && <em className={agent.status === 'failed' ? '' : 'is-warning'}>{agent.error && agent.status !== 'failed' ? '上次失败 · ' : ''}{problem.code || 'Error'}：{problem.summary || problem.message}</em>}
      </button>;
    }) : <p className="runtime-empty">{agents.length ? '没有符合筛选条件的 Agent。' : '运行开始后这里会列出每个 Agent。'}</p>}</div>
  </Card>;
}

function Results({runId}) { const [tab,setTab]=useState('summary'),[data,setData]=useState(null),[error,setError]=useState(''); useEffect(()=>{setData(null);setError('');fetchJson(`/api/runs/${runId}/results/${tab}`).then(setData).catch(e=>setError(e.message));},[runId,tab]); return <Card className="runtime-panel"><div className="runtime-title"><h2>结果数据表</h2><div>{[['summary','每日汇总'],['trades','成交明细'],['agents','Agent 结果']].map(([id,name])=><Button key={id} size="small" type={tab===id?'primary':'text'} onClick={()=>setTab(id)}>{name}</Button>)}</div></div>{error?<p className="runtime-empty">unavailable · {error}</p>:!data?<p className="runtime-empty">读取真实产物…</p>:<div className="runtime-table"><p>来源：{data.source || 'simulation artifacts'} · 最后读取：{new Date().toLocaleString()} {['trades','orders'].includes(tab)&&<>· <a href={`/api/runs/${runId}/results/${tab}/export?format=csv`}>CSV 导出</a> · <a href={`/api/runs/${runId}/results/${tab}/export?format=json`}>JSON 导出</a></>}</p>{data.items?.length?<table><thead><tr>{Object.keys(data.items[0]).slice(0,8).map(key=><th key={key}>{key}</th>)}</tr></thead><tbody>{data.items.map((row,index)=><tr key={index}>{Object.keys(data.items[0]).slice(0,8).map(key=><td key={key}>{typeof row[key]==='object'?JSON.stringify(row[key]):String(row[key] ?? '—')}</td>)}</tr>)}</tbody></table>:<p>unavailable · 当前尚无此类真实产物。</p>}</div>}</Card>; }

export function LiveSimulationView({runId, onBack}) {
  const [run,setRun] = useState(null), [snapshot,setSnapshot] = useState(null), [error,setError] = useState(''), [focus,setFocus] = useState(null), [autoFollow,setAutoFollow] = useState(true), [drawer,setDrawer] = useState(false), [events,setEvents] = useState([]), [streamState,setStreamState] = useState('连接中');
  useEffect(()=>{let alive=true; const read=async()=>{try {const [r,s]=await Promise.all([fetchJson(`/api/runs/${runId}`),fetchJson(`/api/runs/${runId}/runtime`)]);if(alive){setRun(r);setSnapshot(s);setError('');}}catch(e){if(alive)setError(e.message);}};read();const timer=setInterval(read,2000);return()=>{alive=false;clearInterval(timer);};},[runId]);
  useEffect(()=>{const key=`twinmarket:studio:event-cursor:${runId}`;let cursor=Number(sessionStorage.getItem(key) || 0);let source;let timer;let closed=false;let retry=0;const seen=new Set();const connect=()=>{if(closed)return;setStreamState(retry?'重连中':'连接中');source=new EventSource(`/api/runs/${runId}/events?last_event_id=${cursor}`);source.onopen=()=>{retry=0;setStreamState('已连接');};source.addEventListener('runtime',event=>{try {const item=JSON.parse(event.data);if(!Number.isInteger(item.event_id)||item.event_id<=cursor||seen.has(item.event_id))return;seen.add(item.event_id);if(seen.size>500)seen.delete(seen.values().next().value);cursor=item.event_id;sessionStorage.setItem(key,String(cursor));setEvents(previous=>[item,...previous].slice(0,30));if(autoFollow&&item.agent_id)setFocus(item.agent_id);}catch{}});source.onerror=()=>{source.close();if(closed)return;setStreamState('重连中');timer=setTimeout(connect,Math.min(15000,1000*2**Math.min(retry++,4)));};};connect();return()=>{closed=true;clearTimeout(timer);source?.close();setStreamState('已断开');};},[runId,autoFollow]);
  const now = useNow(activeRun(run?.status));
  const focusAgent = useMemo(()=>snapshot?.agents?.find(item=>item.agent_id===focus) || snapshot?.agents?.slice().sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')))[0],[snapshot,focus]);
  async function action(name) { try {await fetchJson(`/api/runs/${runId}/${name}`,{method:'POST'}); const changed=await fetchJson(`/api/runs/${runId}`);setRun(changed);}catch(e){setError(e.message);} }
  if (!run || !snapshot) return <div className="runtime-loading">正在连接 Runtime Observatory…</div>;
  return <section className="runtime-view"><div className="runtime-header"><div><Button type="text" onClick={onBack}>返回实验</Button><h1>{run.name}</h1><p>Run ID: {runId} · 模拟日期：{snapshot.day.date || '—'}</p></div><div className="runtime-actions"><StateTag value={run.status}/><StateTag value={run.health}/>{run.status==='draft'&&<Button type="primary" onClick={()=>action('start')}>运行模拟</Button>}{['failed','cancelled'].includes(run.status)&&<Button type="primary" onClick={()=>action('resume')}>续跑</Button>}{activeRun(run.status)&&<Button onClick={()=>action('stop')}>停止</Button>}<Button onClick={()=>setDrawer(true)} icon={<MagnifyingGlassIcon width={17} height={17} aria-hidden="true" />}>检查</Button></div></div>
    {error&&<p className="runtime-error" role="alert">{error}</p>}<div className="runtime-progress"><article><span>Run</span><strong>{label[run.status] || run.status}</strong></article><article><span>当前阶段</span><strong>{currentStage(snapshot.day)}</strong></article><article><span>Agent</span><strong>{snapshot.agent_counts?.completed || 0} 完成 · {(snapshot.agent_counts?.running || 0) + (snapshot.agent_counts?.retrying || 0)} 进行 · {snapshot.agent_counts?.failed || 0} 失败</strong></article><article><span>事件流</span><strong>{streamState} · #{snapshot.last_event_id}</strong></article></div>
    <DayCycle day={snapshot.day}/><div className="runtime-grid"><AgentBoard agents={snapshot.agents || []} focusId={focusAgent?.agent_id} onFocus={id=>{setFocus(id);setAutoFollow(false);}} timeout={run.config?.agent_timeout ?? 300} now={now}/><div><div className="runtime-title follow-toggle"><Button size="small" type={autoFollow?'primary':'text'} onClick={()=>setAutoFollow(v=>!v)}>自动跟随最新 Agent {autoFollow?'开':'关'}</Button></div><Pipeline agent={focusAgent} now={now} runId={runId} live={activeRun(run.status)}/></div></div>
    <div className="runtime-grid"><Card className="runtime-panel"><h2>Today Summary</h2><div className="summary-grid">{Object.entries(snapshot.today_summary||snapshot.agent_counts||{}).map(([key,value])=><div key={key}><strong>{value}</strong><span>{label[key]||key}</span></div>)}</div></Card><Card className="runtime-panel"><h2>Recent Events</h2><div className="event-list">{events.length?events.map(item=><button key={item.event_id} onClick={()=>item.agent_id&&setFocus(item.agent_id)}><span>#{item.event_id} · {item.scope}/{item.stage}</span><b>{item.message}</b><StateTag value={item.event?.replace('stage_','')}/></button>):<p className="runtime-empty">高层事件会在运行开始后出现。</p>}</div></Card></div>
    {run.status==='completed' && <Results runId={runId}/>}<Drawer open={drawer} title="运行检查" onClose={()=>setDrawer(false)} pushBackground={false} width="min(620px,100vw)"><div className="inspection-drawer"><section className="inspection-summary"><h2>运行状态</h2><p>PID: {run.pid || '—'} · exit code: {run.exit_code ?? '—'}</p><p>{run.failure_reason || '没有未处理的运行级错误。'}</p></section><section className="diagnostics"><h2>诊断事件</h2>{snapshot.diagnostics?.length ? snapshot.diagnostics.map(item=><article className="diagnostic" key={item.event_id}><b>{item.agent_id ? `Agent ${item.agent_id} · ` : ''}{item.scope}/{stageName(item.stage)}{item.substage ? ` · ${item.substage}` : ''} · {item.error_code || '慢阶段'}</b><p>{item.message}</p>{item.error_summary && <p className="diagnostic-detail">{item.error_summary}</p>}</article>) : <p className="diagnostic-empty">当前没有失败、重试或慢阶段事件。</p>}</section><RuntimeLog runId={runId} status={run.status}/></div></Drawer>
  </section>;
}
