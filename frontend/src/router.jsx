import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { Link as RadixLink } from "@radix-ui/themes";

// 极简的 History API 路由：URL 是导航的唯一来源，组件通过 useRouter 读取和跳转。
const RouterContext = createContext(null);

const LEAVE_MESSAGE = "当前页面有未保存的内容，确定离开吗？";

export function parseRoute(pathname, search = "") {
  const query = new URLSearchParams(search);
  const run = /^\/studio\/runs\/([^/]+)(?:\/([a-z]+))?\/?$/.exec(pathname);
  if (run) {
    // 兼容旧地址：/live、/results 和 /agents 已并入运行进度与分析。
    const legacy = { live: ["progress"], results: ["analysis", "market"], agents: ["analysis", "agents"] };
    const [tab, subview] = legacy[run[2]] || [run[2] || null, null];
    return {
      space: "Experiment",
      view: "run",
      runId: decodeURIComponent(run[1]),
      tab,
      subview: query.get("view") || subview,
      agent: query.get("agent"),
    };
  }
  const [, , space, sub] = pathname.split("/");
  if (space === "Experiment" && sub === "new")
    return { space, view: "new", from: query.get("from") };
  return { space: space || "Experiment", view: "index" };
}

export function RouterProvider({ children }) {
  const [location, setLocation] = useState(() => ({
    pathname: window.location.pathname,
    search: window.location.search,
  }));
  // 有未保存内容的页面登记一条离开确认；跳转前统一询问。
  const guard = useRef(false);
  const current = useRef(location);
  current.current = location;

  useEffect(() => {
    const pop = () => {
      if (guard.current && !window.confirm(LEAVE_MESSAGE)) {
        // 用户取消离开：把地址栏恢复为当前页面。
        const { pathname, search } = current.current;
        history.pushState({ studio: true }, "", pathname + search);
        return;
      }
      guard.current = false;
      setLocation({
        pathname: window.location.pathname,
        search: window.location.search,
      });
    };
    const unload = (event) => {
      if (guard.current) event.preventDefault();
    };
    window.addEventListener("popstate", pop);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("popstate", pop);
      window.removeEventListener("beforeunload", unload);
    };
  }, []);

  const navigate = (to, { replace = false, force = false } = {}) => {
    if (!force && guard.current && !window.confirm(LEAVE_MESSAGE)) return false;
    guard.current = false;
    const url = new URL(to, window.location.origin);
    const samePage = url.pathname === current.current.pathname;
    // 每条站内记录都带 studio 标记，返回按钮据此判断能否安全地 history.back()。
    history[replace ? "replaceState" : "pushState"]({ studio: true }, "", url.pathname + url.search);
    setLocation({ pathname: url.pathname, search: url.search });
    if (!samePage) window.scrollTo(0, 0);
    return true;
  };

  // 返回上一页；直接打开深链接时没有站内历史，则回到 fallback。
  const back = (fallback) => {
    if (history.state?.studio && history.length > 1) {
      if (guard.current && !window.confirm(LEAVE_MESSAGE)) return;
      guard.current = false;
      history.back();
    } else navigate(fallback, { replace: true });
  };

  const setGuard = (value) => {
    guard.current = !!value;
  };

  return (
    <RouterContext.Provider
      value={{
        ...location,
        route: parseRoute(location.pathname, location.search),
        navigate,
        back,
        setGuard,
      }}
    >
      {children}
    </RouterContext.Provider>
  );
}

export const useRouter = () => useContext(RouterContext);

// 站内链接：普通点击走 History API，修饰键保留浏览器“新标签页打开”的默认行为。
export function Link({ to, children, onClick, ...props }) {
  const { navigate } = useRouter();
  return (
    <RadixLink
      href={to}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
        event.preventDefault();
        navigate(to);
      }}
      {...props}
    >
      {children}
    </RadixLink>
  );
}
