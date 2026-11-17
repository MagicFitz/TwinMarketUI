import React, { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Flex, Heading, SegmentedControl, Text } from '@radix-ui/themes';
import { StatusBadge } from '../ui.jsx';

// trading_agent.py / utility.py 中发起 LLM 调用的函数 → 可读名称
const STEP_NAME = {
  _get_environment_info: '环境感知',
  _intention_agent: '发帖意图',
  _desire_agent: '信息检索',
  _forum_action: '论坛互动',
  _update_belief: '更新信念',
  _choose_stocks: '选股',
  _data_collection: '技术数据',
  _make_final_decision: '最终决策',
  _read_news: '阅读新闻',
  _polish_decision: '润色决策',
  parse_response_yaml: '修复 YAML',
  parse_response_json: '修复 JSON',
};
// 调用按 BDI 归组，依据论文的定义：信念来自读帖和读新闻，愿望驱动检索和选股，意图是下单和发帖。
const BDI_GROUPS = [
  ['Belief', ['_forum_action', '_read_news', '_get_environment_info', '_update_belief']],
  ['Desire', ['_desire_agent', '_choose_stocks', '_data_collection']],
  ['Intention', ['_make_final_decision', '_polish_decision', '_intention_agent']],
];
// 修复 YAML/JSON 的调用没有自己的归属，跟随它修复的上一次调用。
function groupCalls(calls) {
  const groups = new Map(BDI_GROUPS.map(([name]) => [name, []]));
  let current = 'Belief';
  for (const call of calls) {
    current = BDI_GROUPS.find(([, steps]) => steps.includes(call.step))?.[0] || current;
    groups.get(current).push(call);
  }
  return [...groups];
}
const ROLE_NAME = {system: '系统', user: '用户', assistant: '模型'};
const tokens = value => value == null ? '—' : value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);

async function fetchContext(runId, agentId, date) {
  const query = date ? `?date=${encodeURIComponent(date)}` : '';
  const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agentId)}/llm-context${query}`);
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || '读取失败');
  return response.json();
}

function useLLMContext(runId, agentId, date, live) {
  const [state, setState] = useState({data: null, error: ''});
  useEffect(() => {
    if (!agentId) return undefined;
    let alive = true;
    const read = () => fetchContext(runId, agentId, date)
      .then(data => alive && setState({data, error: ''}))
      .catch(error => alive && setState(previous => ({...previous, error: error.message})));
    read();
    const timer = live ? setInterval(read, 3000) : null;
    return () => { alive = false; if (timer) clearInterval(timer); };
  }, [runId, agentId, date, live]);
  return state;
}

// 一次调用的上下文占用：每段是一条消息，颜色区分角色，本次新增的消息用实色。
function ContextBar({call, messages, contextWindow}) {
  const total = Math.max(contextWindow || 0, call.est_input_tokens + (call.max_tokens || 0), 1);
  const fresh = new Set(call.new_ids);
  return <div className="ctx-bar" title={`估算输入 ${call.est_input_tokens} / 上下文 ${contextWindow}`}>
    {call.message_ids.map((id, index) => {
      const message = messages[id] || {};
      return <i key={`${id}-${index}`} className={`role-${message.role} ${fresh.has(id) ? 'is-new' : ''}`}
        style={{width: `${100 * (message.est_tokens || 0) / total}%`}}/>;
    })}
    {call.max_tokens ? <i className="output" style={{width: `${100 * call.max_tokens / total}%`}} title={`输出上限 ${call.max_tokens}`}/> : null}
  </div>;
}

function MessageItem({message, isNew}) {
  const [open, setOpen] = useState(false);
  const preview = message.content.length > 220 && !open ? `${message.content.slice(0, 220)}…` : message.content;
  return <article className={`ctx-message role-${message.role}`}>
    <header>
      <Badge variant="soft" color="gray">{ROLE_NAME[message.role] || message.role}</Badge>
      {isNew && <Badge variant="soft" color="amber">本次新增</Badge>}
      <span>约 {tokens(message.est_tokens)} tokens · {message.chars} 字符</span>
      {message.content.length > 220 && <Button size="1" variant="ghost" color="gray" onClick={() => setOpen(value => !value)}>{open ? '收起' : '展开全文'}</Button>}
    </header>
    <pre>{preview}</pre>
  </article>;
}

function CallRow({call, record, open, onToggle}) {
  const real = call.prompt_tokens;
  const truncated = call.finish_reason === 'length';
  const fresh = new Set(call.new_ids);
  const newTokens = call.message_ids.filter(id => fresh.has(id)).reduce((sum, id) => sum + (record.messages[id]?.est_tokens || 0), 0);
  return <section className={`ctx-call ${call.status === 'error' ? 'is-error' : ''}`}>
    <button className="ctx-call-head" onClick={onToggle} aria-expanded={open}>
      <b>#{call.seq} {STEP_NAME[call.step] || call.step}</b>
      <span>{call.message_ids.length} 条消息 · 新增约 {tokens(newTokens)}</span>
      <span>估算 {tokens(call.est_input_tokens)}{real != null ? ` · 实际 ${tokens(real)}` : ''} / {tokens(record.context_window)}</span>
      <span>{call.status === 'error' ? '失败' : truncated ? '输出被截断' : `输出 ${tokens(call.completion_tokens)} / 上限 ${tokens(call.max_tokens)}`}</span>
      <ContextBar call={call} messages={record.messages} contextWindow={record.context_window}/>
    </button>
    {open && <div className="ctx-call-body">
      <p className="ctx-meta">函数 {call.step} · 阶段 {call.stage || '—'} · 耗时 {(call.latency_ms / 1000).toFixed(1)} 秒{call.finish_reason ? ` · finish_reason=${call.finish_reason}` : ''}</p>
      {call.error && <p className="ctx-error">{call.error}</p>}
      {call.message_ids.map((id, index) => record.messages[id]
        ? <MessageItem key={`${id}-${index}`} message={record.messages[id]} isNew={fresh.has(id)}/> : null)}
      {call.reply_id && record.messages[call.reply_id] && <>
        <h4>模型回复</h4>
        <MessageItem message={record.messages[call.reply_id]} isNew={false}/>
      </>}
    </div>}
  </section>;
}

function groupStatus(calls, active) {
  if (calls.some(call => call.status === 'error')) return 'failed';
  return active ? 'running' : 'completed';
}

export function LLMContext({runId, agentId, date, live}) {
  const [attemptIndex, setAttemptIndex] = useState(-1);
  const [openSeq, setOpenSeq] = useState(null);
  const {data, error} = useLLMContext(runId, agentId, date, live);
  const attempts = data?.attempts || [];
  const record = attempts[attemptIndex < 0 ? attempts.length - 1 : attemptIndex];
  const peak = useMemo(() => (record?.calls || []).reduce((best, call) => call.est_input_tokens > (best?.est_input_tokens || 0) ? call : best, null), [record]);

  if (error && !data) return <Text as="p" size="2" color="red">读取失败：{error}</Text>;
  if (!data) return <Text as="p" size="2" color="gray">读取中…</Text>;
  if (!record) return <Text as="p" size="2" color="gray">{date} 没有 LLM 调用记录</Text>;
  const groups = groupCalls(record.calls);
  const lastGroup = groups.filter(([, calls]) => calls.length).at(-1)?.[0];
  return <div className="ctx-inspector">
    <div className="ctx-summary">
      <div><span>LLM 调用</span><strong>{record.calls.length} 次</strong></div>
      <div><span>最大输入（估算）</span><strong>{tokens(peak?.est_input_tokens)} / {tokens(record.context_window)}</strong></div>
      <div><span>最大输入步骤</span><strong>{peak ? (STEP_NAME[peak.step] || peak.step) : '—'}</strong></div>
      <div><span>模型</span><strong>{record.model}</strong></div>
    </div>
    <div className="ctx-controls">
      {attempts.length > 1 && <SegmentedControl.Root size="1" value={String(attemptIndex < 0 ? attempts.length - 1 : attemptIndex)} onValueChange={value => setAttemptIndex(Number(value))}>
        {attempts.map((item, index) => <SegmentedControl.Item key={item.attempt} value={String(index)}>第 {item.attempt} 次尝试</SegmentedControl.Item>)}
      </SegmentedControl.Root>}
      <span className="ctx-legend"><i className="role-system"/>系统 <i className="role-user"/>用户 <i className="role-assistant"/>模型 <i className="output"/>输出上限</span>
    </div>
    {groups.filter(([, calls]) => calls.length).map(([name, calls]) => <section key={name} className="bdi-group">
      <Flex align="center" gap="2">
        <Heading as="h4" size="2">{name}</Heading>
        <StatusBadge value={groupStatus(calls, live && name === lastGroup)}/>
        <Text size="1" color="gray">{calls.length} 次调用</Text>
      </Flex>
      {calls.map(call => <CallRow key={call.seq} call={call} record={record}
        open={openSeq === call.seq} onToggle={() => setOpenSeq(value => value === call.seq ? null : call.seq)}/>)}
    </section>)}
  </div>;
}
