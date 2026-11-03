import { useEffect, useState } from "react";

// Studio 共享的请求、格式化与状态口径，避免各空间各写一套。

export async function fetchJson(url, init) {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || body.detail || "请求失败");
  }
  return response.status === 204 ? null : response.json();
}

export const postJson = (url, body) =>
  fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

/**
 * 读取 Runtime API，并可选地按 interval 轮询。
 *
 * 保留同一 URL 的上一份数据，可让刷新过程避免闪烁；AbortController 和 alive 标记则
 * 防止组件卸载或请求切换后继续写入状态。
 */
export function useData(url, refresh = 0, interval = 0) {
  const [state, set] = useState({ data: null, error: null, loading: true });
  useEffect(() => {
    let alive = true,
      pending = false;
    const controller = new AbortController();
    set((s) => ({
      data: s.url === url ? s.data : null,
      url,
      error: null,
      loading: true,
    }));
    async function read() {
      if (pending || !url) return;
      pending = true;
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) throw new Error("数据连接暂不可用");
        const data = await response.json();
        if (alive)
          set((previous) => ({
            data:
              previous.url === url &&
              JSON.stringify(previous.data) === JSON.stringify(data)
                ? previous.data
                : data,
            url,
            error: null,
            loading: false,
          }));
      } catch (error) {
        if (alive && error.name !== "AbortError")
          set((s) => ({ ...s, error: error.message, loading: false }));
      } finally {
        pending = false;
      }
    }
    read();
    const timer = interval ? setInterval(read, interval) : null;
    return () => {
      alive = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [url, refresh, interval]);
  return state.url && state.url !== url
    ? { data: null, error: null, loading: true }
    : state;
}

// 每秒刷新一次，让运行中阶段的耗时实时变化。
export function useNow(active) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

// 读写浏览器本地偏好；隐私模式下 localStorage 可能抛错，此时退回默认值。
export function usePreference(key, fallback) {
  const [value, setValue] = useState(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored == null ? fallback : JSON.parse(stored);
    } catch {
      return fallback;
    }
  });
  const update = (next) =>
    setValue((previous) => {
      const resolved = typeof next === "function" ? next(previous) : next;
      try {
        localStorage.setItem(key, JSON.stringify(resolved));
      } catch {
        /* 偏好只是便利项，写入失败不影响页面 */
      }
      return resolved;
    });
  return [value, update];
}

// 统一处理空值与中文数字格式，保证表格、指标和提示中的展示口径一致。
export const n = (value) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });

export const clock = (value) =>
  value ? new Date(value).toLocaleTimeString("zh-CN", { hour12: false }) : "—";

export const stamp = (value) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        hour12: false,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

export const duration = (ms) =>
  ms == null
    ? "—"
    : ms < 60000
      ? `${Math.round(ms / 1000)} 秒`
      : `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`;

export const sinceMs = (iso, now) =>
  iso ? Math.max(0, now - Date.parse(iso)) : null;

// 后端运行、阶段与 Agent 状态到界面文案和 Radix 色板的映射。
export const STATUS = {
  draft: ["草稿", "gray"],
  queued: ["排队中", "blue"],
  running: ["运行中", "blue"],
  stopping: ["正在停止", "amber"],
  completed: ["已完成", "green"],
  failed: ["失败", "red"],
  cancelled: ["已取消", "gray"],
  pending: ["待处理", "gray"],
  waiting: ["等待中", "gray"],
  retrying: ["重试中", "amber"],
  skipped: ["已跳过", "gray"],
  healthy: ["健康", "green"],
  degraded: ["降级", "amber"],
  unhealthy: ["异常", "red"],
};

export const isActiveRun = (status) =>
  ["queued", "running", "stopping"].includes(status);

// 运行可执行的主控制动作：草稿可启动，失败可重试，取消可续跑，活动中可停止。
// 重试与续跑都走 /retry：已提交过交易日时从断点继续，否则归档本次输出后从头运行。
export function runControl(status) {
  if (status === "draft") return { action: "start", label: "运行" };
  if (status === "failed") return { action: "retry", label: "重试" };
  if (status === "cancelled") return { action: "retry", label: "续跑" };
  if (["queued", "running"].includes(status))
    return { action: "stop", label: "停止" };
  return null;
}

// 列表接口只有 checkpoint，按自然日估算日期进度；交易日口径在工作台里看。
export function runProgress(run) {
  const start = run.config?.start_date,
    end = run.config?.end_date,
    done = run.checkpoint?.last_completed_date;
  if (!start || !end) return { ratio: 0, label: "—" };
  if (run.status === "completed") return { ratio: 1, label: `完成至 ${end}` };
  if (!done) return { ratio: 0, label: "尚未完成交易日" };
  // 失败的实验即使走完了日期也没有可用结果，不写“完成至”。
  const verb = run.status === "failed" ? "已处理至" : "完成至";
  const day = 86400000;
  const total = (Date.parse(end) - Date.parse(start)) / day + 1;
  const passed = (Date.parse(done) - Date.parse(start)) / day + 1;
  return {
    ratio: Math.max(0, Math.min(1, passed / total)),
    label: `${verb} ${done}`,
  };
}
