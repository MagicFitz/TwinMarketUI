import React from "react";
import {
  Badge,
  Box,
  Callout,
  Card,
  DataList,
  Flex,
  Heading,
  Text,
} from "@radix-ui/themes";
import { ExclamationTriangleIcon } from "@radix-ui/react-icons";
import { STATUS } from "./lib.js";

/*
 * 按钮规范（Radix Themes）：
 *   主操作   variant="solid"              每个区域最多一个（创建实验、运行、续跑）
 *   次操作   variant="soft" color="gray"  停止以外的常规操作（复制、导出、刷新）
 *   危险操作 variant="soft" color="red"   停止、删除，删除需二次确认
 *   行内操作 variant="ghost"              表格行、面包屑旁的轻量操作
 *   视图切换 SegmentedControl / Tabs      不再用主按钮表示“选中”
 */

export const icon = (Component, size = 16) => (
  <Component width={size} height={size} aria-hidden="true" />
);

export function StatusBadge({ value, size = "1" }) {
  const [label, color] = STATUS[value] || [value || "—", "gray"];
  return (
    <Badge color={color} variant="soft" size={size} radius="full">
      {label}
    </Badge>
  );
}

export function PageHeader({ title, description, actions }) {
  return (
    <Flex align="center" justify="between" gap="4" wrap="wrap" mb="5">
      <Box>
        {title && (
          <Heading as="h1" size="6" weight="bold">
            {title}
          </Heading>
        )}
        {description && (
          <Text as="p" size="2" color="gray" mt={title ? "1" : "0"}>
            {description}
          </Text>
        )}
      </Box>
      {actions && (
        <Flex gap="2" align="center" wrap="wrap">
          {actions}
        </Flex>
      )}
    </Flex>
  );
}

export function Panel({ title, actions, children, className = "" }) {
  return (
    <Card size="2" className={`panel ${className}`}>
      {(title || actions) && (
        <Flex align="center" justify="between" gap="3" wrap="wrap" mb="3">
          <Heading as="h2" size="3">
            {title}
          </Heading>
          {actions && (
            <Flex gap="2" align="center" wrap="wrap">
              {actions}
            </Flex>
          )}
        </Flex>
      )}
      {children}
    </Card>
  );
}

export function Empty({ state, children }) {
  return (
    <Flex className="empty" align="center" justify="center">
      <Text size="2" color="gray">
        {children || state?.error || (state?.loading ? "读取中…" : "暂无数据")}
      </Text>
    </Flex>
  );
}

export function ErrorCallout({ children }) {
  if (!children) return null;
  return (
    <Callout.Root color="red" size="1" role="alert" my="3">
      <Callout.Icon>{icon(ExclamationTriangleIcon)}</Callout.Icon>
      <Callout.Text>{children}</Callout.Text>
    </Callout.Root>
  );
}

export function Notice({ children, color = "amber" }) {
  return (
    <Callout.Root color={color} size="1" variant="surface" my="3">
      <Callout.Text>{children}</Callout.Text>
    </Callout.Root>
  );
}

export function Definition({ values, size = "2" }) {
  return (
    <DataList.Root size={size}>
      {values.map(([key, value]) => (
        <DataList.Item key={key}>
          <DataList.Label minWidth="96px">{key}</DataList.Label>
          <DataList.Value className="break">{value ?? "未记录"}</DataList.Value>
        </DataList.Item>
      ))}
    </DataList.Root>
  );
}

export function Stat({ label, value }) {
  return (
    <Card size="2" className="stat">
      <Text size="1" color="gray">
        {label}
      </Text>
      <Text as="div" size="5" weight="bold" mt="1" className="tabular">
        {value}
      </Text>
    </Card>
  );
}
