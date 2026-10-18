import React, { useEffect, useState } from "react";
import { Badge, Box, Flex, IconButton, Text, Theme, Tooltip } from "@radix-ui/themes";
import {
  ChevronRightIcon,
  CubeIcon,
  FileTextIcon,
  GearIcon,
  GlobeIcon,
  PersonIcon,
  ReloadIcon,
  RocketIcon,
} from "@radix-ui/react-icons";
import { useData, usePreference } from "./lib.js";
import { icon } from "./ui.jsx";
import { Link, RouterProvider, useRouter } from "./router.jsx";
import { ExperimentList } from "./experiments/ExperimentList.jsx";
import { NewExperiment } from "./experiments/NewExperiment.jsx";
import { Workspace, WORKSPACE_TABS } from "./experiments/Workspace.jsx";
import { AgentSpace, MarketSpace, SettingSpace } from "./spaces.jsx";
import { Documentation } from "./Documentation.jsx";

// 左侧导航的路由段、显示名称和图标。
const spaces = [
  { id: "Experiment", name: "实验", hint: "创建、运行与观察", icon: RocketIcon },
  { id: "Agent", name: "智能体", hint: "投资者群体", icon: PersonIcon },
  { id: "Market", name: "市场", hint: "环境与资产", icon: GlobeIcon },
  { id: "Setting", name: "设置", hint: "模型与偏好", icon: GearIcon },
  { id: "Documentation", name: "文档", hint: "项目说明", icon: FileTextIcon },
];

// 面包屑由路由推导；运行名称单独读取一次，避免依赖工作台内部状态。
function Breadcrumb({ route }) {
  const runState = useData(route.view === "run" ? `/api/runs/${encodeURIComponent(route.runId)}` : null);
  const space = spaces.find((s) => s.id === route.space) || spaces[0];
  const crumbs = [{ label: space.name, to: `/studio/${space.id}` }];
  if (route.view === "new") crumbs.push({ label: "新建实验" });
  if (route.view === "run") {
    crumbs.push({
      label: runState.data?.name || route.runId,
      to: `/studio/runs/${encodeURIComponent(route.runId)}`,
    });
    const tab = WORKSPACE_TABS.find(([id]) => id === route.tab);
    if (tab && tab[0] !== WORKSPACE_TABS[0][0]) crumbs.push({ label: tab[1] });
  }
  return (
    <nav aria-label="面包屑" className="breadcrumb">
      {crumbs.map((crumb, index) => {
        const last = index === crumbs.length - 1;
        return (
          <Flex key={index} align="center" gap="2" minWidth="0">
            {index > 0 && <Text color="gray">{icon(ChevronRightIcon, 14)}</Text>}
            {last || !crumb.to ? (
              <Text size="2" weight={last ? "medium" : "regular"} color={last ? undefined : "gray"} truncate aria-current={last ? "page" : undefined}>
                {crumb.label}
              </Text>
            ) : (
              <Link to={crumb.to} size="2" color="gray" underline="hover" truncate>
                {crumb.label}
              </Link>
            )}
          </Flex>
        );
      })}
    </nav>
  );
}

function Shell() {
  const { route, pathname, navigate } = useRouter();
  const [compact, setCompact] = usePreference("twin-density-compact", false);
  const [refresh, setRefresh] = useState(0);
  const catalog = useData("/api/catalog", refresh);

  // 旧的 /studio 根地址重定向到实验空间。
  useEffect(() => {
    if (pathname === "/studio" || pathname === "/studio/") navigate("/studio/Experiment", { replace: true });
  }, [pathname]);

  const needsCatalog = ["Agent", "Market", "Setting"].includes(route.space);
  return (
    <Theme accentColor="teal" grayColor="slate" radius="medium" scaling={compact ? "90%" : "100%"}>
      <div className="app">
        <aside className="sidebar">
          <a className="brand" href="/studio/Experiment" onClick={(e) => { if (!e.metaKey && !e.ctrlKey) { e.preventDefault(); navigate("/studio/Experiment"); } }}>
            {icon(CubeIcon, 24)}
            <span>
              TwinMarket<small>Research Studio</small>
            </span>
          </a>
          <nav aria-label="主导航">
            {spaces.map((s) => (
              <a
                key={s.id}
                href={`/studio/${s.id}`}
                aria-current={route.space === s.id ? "page" : undefined}
                className={route.space === s.id ? "active" : ""}
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                  e.preventDefault();
                  navigate(`/studio/${s.id}`);
                }}
              >
                {icon(s.icon, 18)}
                <span>
                  {s.name}
                  <small>{s.hint}</small>
                </span>
              </a>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <Badge color="gray" variant="soft">本地 · Runtime API</Badge>
          </div>
        </aside>
        <div className="workspace">
          <header className="topbar">
            <Breadcrumb route={route} />
            <Tooltip content="立即刷新">
              <IconButton variant="ghost" color="gray" aria-label="立即刷新" onClick={() => setRefresh((v) => v + 1)}>
                {icon(ReloadIcon)}
              </IconButton>
            </Tooltip>
          </header>
          <Box asChild className="main">
            <main>
              {route.space === "Experiment" && route.view === "index" && <ExperimentList refresh={refresh} />}
              {route.space === "Experiment" && route.view === "new" && <NewExperiment from={route.from} />}
              {route.view === "run" && <Workspace key={route.runId} runId={route.runId} tab={route.tab} agent={route.agent} refresh={refresh} />}
              {needsCatalog && !catalog.data && (
                <Text as="p" color="gray" size="2" className="empty">
                  {catalog.error || "读取中…"}
                </Text>
              )}
              {route.space === "Agent" && catalog.data && <AgentSpace catalog={catalog.data} refresh={refresh} />}
              {route.space === "Market" && catalog.data && <MarketSpace catalog={catalog.data} refresh={refresh} />}
              {route.space === "Setting" && catalog.data && <SettingSpace catalog={catalog.data} compact={compact} setCompact={setCompact} />}
              {route.space === "Documentation" && <Documentation refresh={refresh} />}
            </main>
          </Box>
        </div>
      </div>
    </Theme>
  );
}

export function App() {
  return (
    <RouterProvider>
      <Shell />
    </RouterProvider>
  );
}
