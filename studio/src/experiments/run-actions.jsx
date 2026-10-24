import React, { useState } from "react";
import { AlertDialog, Button, DropdownMenu, Flex } from "@radix-ui/themes";
import { BarChartIcon, PauseIcon, PlayIcon, ResetIcon } from "@radix-ui/react-icons";
import { fetchJson, isActiveRun, postJson, runControl } from "../lib.js";
import { icon } from "../ui.jsx";
import { useRouter } from "../router.jsx";

// 列表、卡片与工作台共用同一套运行控制，保证按钮文案、样式和确认逻辑一致。
export function useRunActions(onChanged) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  const { navigate } = useRouter();
  const perform = async (run, action, request) => {
    setBusy(`${run.run_id}:${action}`);
    setError("");
    try {
      await request();
      onChanged?.(action);
      return true;
    } catch (reason) {
      setError(`${run.name}：${reason.message}`);
      return false;
    } finally {
      setBusy(null);
    }
  };
  return {
    busy,
    error,
    isBusy: (run, action) => busy === `${run.run_id}:${action}`,
    control: (run, action) => perform(run, action, () => postJson(`/api/runs/${encodeURIComponent(run.run_id)}/${action}`)),
    archive: (run) => perform(run, "archive", () => postJson(`/api/runs/${encodeURIComponent(run.run_id)}/archive`)),
    remove: (run) =>
      perform(run, "delete", () => fetchJson(`/api/runs/${encodeURIComponent(run.run_id)}`, { method: "DELETE" })),
    // 复制不直接落库，而是带着原配置进入创建页，让用户确认或修改后再创建。
    duplicate: (run) => navigate(`/studio/Experiment/new?from=${encodeURIComponent(run.run_id)}`),
  };
}

// 每种操作一个色调：运行 teal、续跑 blue、停止 red、结果 iris，列表里一眼可分。
const CONTROL_STYLE = {
  start: { icon: PlayIcon, variant: "solid", color: "teal" },
  resume: { icon: ResetIcon, variant: "solid", color: "blue" },
  stop: { icon: PauseIcon, variant: "soft", color: "red" },
};

export function RunControlButton({ run, actions, size = "2" }) {
  const control = runControl(run.status);
  if (!control) return null;
  const style = CONTROL_STYLE[control.action];
  return (
    <Button
      size={size}
      variant={style.variant}
      color={style.color}
      loading={actions.isBusy(run, control.action)}
      disabled={!!actions.busy}
      onClick={() => actions.control(run, control.action)}
    >
      {icon(style.icon, size === "1" ? 12 : 14)}
      {control.label}
    </Button>
  );
}

// 已完成的实验没有运行控制，列表和卡片里改为直达结果页。
export function RunResultButton({ run, size = "2" }) {
  const { navigate } = useRouter();
  if (run.status !== "completed") return null;
  return (
    <Button size={size} variant="soft" color="iris" onClick={() => navigate(`/studio/runs/${encodeURIComponent(run.run_id)}/results`)}>
      {icon(BarChartIcon, size === "1" ? 12 : 14)}
      结果
    </Button>
  );
}

export function RunMoreMenu({ run, actions, children, onDeleted }) {
  const [confirming, setConfirming] = useState(false);
  const active = isActiveRun(run.status);
  return (
    <>
      <DropdownMenu.Root>
        <DropdownMenu.Trigger>{children}</DropdownMenu.Trigger>
        <DropdownMenu.Content align="end">
          <DropdownMenu.Item onSelect={() => actions.duplicate(run)}>复制为新实验</DropdownMenu.Item>
          <DropdownMenu.Item disabled={active} onSelect={() => actions.archive(run)}>
            归档
          </DropdownMenu.Item>
          <DropdownMenu.Separator />
          <DropdownMenu.Item color="red" disabled={run.status !== "draft"} onSelect={() => setConfirming(true)}>
            删除草稿
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Root>
      <AlertDialog.Root open={confirming} onOpenChange={setConfirming}>
        <AlertDialog.Content maxWidth="420px">
          <AlertDialog.Title>删除“{run.name}”？</AlertDialog.Title>
          <AlertDialog.Description size="2">
            草稿的配置与输出目录会被永久删除，无法恢复。已运行过的实验只能归档。
          </AlertDialog.Description>
          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray">
                取消
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button
                color="red"
                onClick={async () => {
                  if (await actions.remove(run)) onDeleted?.();
                }}
              >
                删除
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>
    </>
  );
}
