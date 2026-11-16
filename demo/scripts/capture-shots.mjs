// 为介绍视频截取界面素材：实验管理（列表、新建、运行进度、日志）与分析（合成示例实验 demo-synthetic-100a）。
// 需先启动 backend、frontend 与 market-service：node demo/scripts/capture-shots.mjs
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { mkdir } from 'node:fs/promises';

const base = process.env.STUDIO_URL || 'http://127.0.0.1:5173';
const run = process.env.RUN_ID || 'demo-synthetic-100a';
const out = new URL('../shots/', import.meta.url).pathname;
await mkdir(out, { recursive: true });

// 实验列表里不出镜的开发调试记录（副本、目录重构验证、测试运行）
const HIDDEN_RUNS = new Set(['20261017_1-副本', '10agent_15days_test', '20261103-155940-9918', '20261106-163840-89e3']);

const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
await page.route(/\/api\/runs\?/, async route => {
  const response = await route.fetch();
  const body = await response.json();
  body.items = body.items.filter(item => !HIDDEN_RUNS.has(item.run_id));
  body.total = body.items.length;
  await route.fulfill({ response, json: body });
});

// 合成数据横幅在截图中隐去，避免画面拥挤（视频里另有说明位置）。
const hideBanner = () => page.evaluate(() => {
  for (const el of document.querySelectorAll('div')) {
    if (el.childElementCount < 4 && el.textContent.startsWith('合成示例数据')) el.closest('.rt-CalloutRoot')?.remove();
  }
});

async function open(path) {
  await page.goto(`${base}${path}`);
  await page.locator('h1').first().waitFor().catch(() => {});
  await page.waitForTimeout(2500); // 等 ECharts 入场动画结束
  await hideBanner();
  await page.waitForTimeout(400);
}

const analysis = view => open(`/studio/runs/${run}/analysis?view=${view}`);
const field = label => page.locator('label', { hasText: label }).locator('input, textarea').first();

// [文件名, 准备步骤, 截图视口高度]
const shots = [
  ['exp-list', async () => {
    await open('/studio/Experiment');
    await page.evaluate(() => localStorage.clear());
    await open('/studio/Experiment');
    await page.getByRole('radio', { name: '卡片视图' }).click();
    await page.locator('.run-card').first().waitFor();
    await page.waitForTimeout(600);
  }],
  ['exp-new', async () => {
    await open('/studio/Experiment/new');
    await field('实验别名').fill('谣言冲击 · 100 Agent');
    await field('描述').fill('第 10 个交易日注入负面谣言，观察信念分化与卖出意愿');
    await field('Agent 数量').fill('100');
    await field('并发 Worker').fill('8');
    await page.getByText('展开高级参数').click();
    await field('激活概率').fill('0.8');
    await field('技术面概率').fill('0.5');
    await page.evaluate(() => document.activeElement?.blur()); // 去掉输入框的焦点描边
    await page.waitForTimeout(1500);
  }, 1240],
  ['exp-progress', () => open(`/studio/runs/${run}/progress`)],
  ['exp-logs', () => open(`/studio/runs/${run}/logs`)],
  ['market', () => analysis('market')],
  ['trading', () => analysis('trading')],
  ['propagation', () => analysis('propagation')],
  ['stylized', () => analysis('stylized')],
  ['bdi', async () => {
    await open(`/studio/runs/${run}/analysis?view=agents&agent=10096603837`);
    await page.getByRole('tab', { name: 'BDI' }).click();
    await page.waitForTimeout(800);
    await page.getByText('12-26', { exact: true }).first().click();
    await page.waitForTimeout(800);
  }],
];

const only = process.argv[2]?.split(',');
for (const [name, prepare, height = 1000] of shots) {
  if (only && !only.includes(name)) continue;
  await page.setViewportSize({ width: 1600, height });
  await prepare();
  await page.screenshot({ path: `${out}${name}.png` });
  console.log(`${out}${name}.png`);
}
await browser.close();
