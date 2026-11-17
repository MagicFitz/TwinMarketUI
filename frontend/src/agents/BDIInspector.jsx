import React, { useEffect, useRef, useState } from "react";
import { Box, Card, Flex, Grid, Heading, Text } from "@radix-ui/themes";
import { useData } from "../lib.js";
import { Empty } from "../ui.jsx";
import { LLMContext } from "./LLMContextInspector.jsx";

/*
 * 一个 Agent 逐日的 Belief、Desire、Intention，每天都与上一个交易日对比。
 * 红色表示看涨和买入，蓝色表示看空和卖出：沿用 A 股红涨习惯，又不依赖红绿区分。
 */

const STANCE = {
  bullish: { label: "看涨", short: "涨", tone: "up" },
  neutral: { label: "中性", short: "中", tone: "flat" },
  bearish: { label: "看空", short: "空", tone: "down" },
  unknown: { label: "未知", short: "—", tone: "none" },
};
const SHIFT = {
  increase: { label: "想加仓", short: "▲", tone: "up" },
  decrease: { label: "想减仓", short: "▼", tone: "down" },
  maintain: { label: "维持仓位", short: "=", tone: "flat" },
  inactive: { label: "未激活", short: "·", tone: "none" },
  unknown: { label: "无记录", short: "—", tone: "none" },
  closed: { label: "休市", short: "·", tone: "none" },
};
const DIRECTION = {
  buy: { label: "买入", short: "买", tone: "up" },
  sell: { label: "卖出", short: "卖", tone: "down" },
  mixed: { label: "调仓", short: "调", tone: "mixed" },
  hold: { label: "持有", short: "持", tone: "flat" },
  none: { label: "无交易", short: "—", tone: "none" },
};
const ACTION = { buy: "买入", sell: "卖出" };
const BELIEF_SOURCE = { post: "当日帖子", llm: "当日 LLM 回复", carried: "沿用上一日" };
const POST_TYPE = { type1: "事件点评", type2: "交易总结", type3: "未来展望" };

const shiftOf = (day) => (day.trading ? day.desire.shift : "closed");
const directionOf = (day) => day.intention.direction;
const percent = (value) => (value == null ? "—" : `${Number(value).toFixed(1)}%`);
const signed = (value) => `${value > 0 ? "+" : ""}${Number(value).toFixed(1)}`;
const compareLabel = (day) => (day.previous.date ? `较 ${day.previous.date.slice(5)}` : "较初始");

function Pill({ item }) {
  return <span className={`bdi-pill tone-${item.tone}`}>{item.label}</span>;
}

function Timeline({ days, selected, onSelect }) {
  const scroller = useRef(null);
  // 长时间运行时默认看到最近的日期。
  useEffect(() => {
    if (scroller.current) scroller.current.scrollLeft = scroller.current.scrollWidth;
  }, []);
  return (
    <div className="bdi-matrix">
      <div className="bdi-labels" aria-hidden="true">
        <span />
        <span>Belief</span>
        <span>Desire</span>
        <span>Intention</span>
      </div>
      <div className="bdi-days" ref={scroller}>
        {days.map((day) => {
          const stance = STANCE[day.belief.stance] || STANCE.unknown;
          const shift = SHIFT[shiftOf(day)];
          const direction = DIRECTION[directionOf(day)];
          const summary = [
            day.date,
            stance.label,
            day.belief.changed ? "立场转变" : null,
            shift.label,
            direction.label,
            day.intention.posted ? "已发帖" : "未发帖",
            day.status === "failed" ? "失败" : null,
          ];
          return (
            <button
              key={day.date}
              type="button"
              className={`bdi-day ${day.status === "failed" ? "is-failed" : ""}`}
              aria-pressed={day.date === selected}
              aria-label={summary.filter(Boolean).join("，")}
              title={summary.filter(Boolean).join(" · ")}
              onClick={() => onSelect(day.date)}
            >
              <time>{day.date.slice(5)}</time>
              <i className={`bdi-cell tone-${stance.tone} ${day.belief.changed ? "is-changed" : ""}`}>{stance.short}</i>
              <i className={`bdi-cell tone-${shift.tone}`}>{shift.short}</i>
              <i className={`bdi-cell tone-${direction.tone}`}>
                {direction.short}
                {day.intention.posted && <b className="bdi-post" />}
              </i>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <Card size="1">
      <Flex direction="column" gap="2">
        <Heading as="h3" size="2">
          {title}
        </Heading>
        {children}
      </Flex>
    </Card>
  );
}

function Fold({ label, children }) {
  return (
    <details className="disclosure">
      <summary>
        <Text size="1">{label}</Text>
      </summary>
      <Box mt="2">{children}</Box>
    </details>
  );
}

function Belief({ day }) {
  const { belief, previous } = day;
  const before = STANCE[previous.stance] || STANCE.unknown;
  return (
    <Section title="Belief">
      <Flex align="center" gap="2" wrap="wrap">
        <Pill item={STANCE[belief.stance] || STANCE.unknown} />
        <Text size="1" color="gray">
          {compareLabel(day)}：{belief.changed ? `${before.label} → ${(STANCE[belief.stance] || STANCE.unknown).label}` : "不变"}
        </Text>
      </Flex>
      {belief.evidence && (
        <Text as="p" size="2">
          “{belief.evidence}”
        </Text>
      )}
      <Text size="1" color="gray">
        来源：{BELIEF_SOURCE[belief.source]}
      </Text>
      {belief.text && (
        <Fold label="信念全文">
          <Text as="p" size="1" className="pre-wrap">
            {belief.text}
          </Text>
        </Fold>
      )}
    </Section>
  );
}

function Desire({ day }) {
  const { desire } = day;
  return (
    <Section title="Desire">
      <Flex align="center" gap="2" wrap="wrap">
        <Pill item={SHIFT[shiftOf(day)]} />
        {desire.position_delta != null && (
          <Text size="1" color="gray" className="tabular">
            {compareLabel(day)}：{signed(desire.position_delta)} 个百分点
          </Text>
        )}
      </Flex>
      {desire.target_position != null && (
        <Text size="2" className="tabular">
          仓位 {percent(desire.held_position)} → 目标 {percent(desire.target_position)}
        </Text>
      )}
      {(desire.focus_added.length > 0 || desire.focus_removed.length > 0) && (
        <Text size="1" color="gray">
          关注：{desire.focus_added.map((code) => `+${code}`).concat(desire.focus_removed.map((code) => `−${code}`)).join(" ")}
        </Text>
      )}
      {desire.queries.length > 0 && (
        <Fold label={`检索问题 ${desire.queries.length}`}>
          <ul className="plain-list">
            {desire.queries.map((query) => (
              <li key={query}>
                <Text size="1">{query}</Text>
              </li>
            ))}
          </ul>
        </Fold>
      )}
    </Section>
  );
}

function Intention({ day }) {
  const { intention } = day;
  return (
    <Section title="Intention">
      <Flex align="center" gap="2" wrap="wrap">
        <Pill item={DIRECTION[directionOf(day)]} />
        <Text size="1" color="gray">
          {intention.posted ? `已发帖 · ${POST_TYPE[intention.post.type] || intention.post.type}` : "未发帖"}
          {intention.reposts > 0 ? ` · 转发 ${intention.reposts}` : ""}
        </Text>
      </Flex>
      {intention.orders.length > 0 && (
        <ul className="bdi-orders">
          {intention.orders.map((order) => (
            <li key={order.stock_id}>
              <span>
                {ACTION[order.action]} {order.name || order.stock_id}
              </span>
              <Text size="1" color="gray" className="tabular">
                {percent(order.cur_position)} → {percent(order.target_position)}
              </Text>
            </li>
          ))}
        </ul>
      )}
      {intention.post && (
        <Fold label="帖子内容">
          <Text as="p" size="1" className="pre-wrap">
            {intention.post.content}
          </Text>
        </Fold>
      )}
    </Section>
  );
}

export function BDIInspector({ runId, agentId, live = false, children }) {
  const state = useData(
    `/api/runs/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agentId)}/bdi`,
    0,
    live ? 5000 : 0,
  );
  const [selected, setSelected] = useState(null);
  const days = state.data?.days;
  if (!days) return <Empty state={state}>{state.error ? "该 Agent 暂无 BDI 记录。" : undefined}</Empty>;
  if (!days.length) return <Empty>该 Agent 还没有模拟日。</Empty>;
  const day = days.find((item) => item.date === selected) || days[days.length - 1];
  return (
    <Flex direction="column" gap="4">
      {children}
      <Box>
        <Timeline days={days} selected={day.date} onSelect={setSelected} />
        <Text as="p" size="1" color="gray" mt="1">
          看涨、看空由规则从信念文本推断，可能误判，请对照原文。黑框表示立场转变，黄点表示当日发帖。
        </Text>
      </Box>
      {day.error && (
        <Text as="p" size="2" color="red" role="alert">
          {day.date} 失败：{day.error.code || "Error"} {day.error.summary || ""}
        </Text>
      )}
      <Grid columns={{ initial: "1", sm: "3" }} gap="3" align="start">
        <Belief day={day} />
        <Desire day={day} />
        <Intention day={day} />
      </Grid>
      <LLMContext key={day.date} runId={runId} agentId={agentId} date={day.date} live={live && day === days[days.length - 1]} />
    </Flex>
  );
}
