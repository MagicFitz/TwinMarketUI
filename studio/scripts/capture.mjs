// 为设计评审截取 Studio 主要页面：桌面、平板与手机三种宽度。需先运行 script/dev.sh。
//   STUDIO_URL=http://127.0.0.1:5173 npm run qa:capture
import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';

const base = process.env.STUDIO_URL || 'http://127.0.0.1:5173';
const out = 'qa/radix';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const errors = [];

try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  page.on('pageerror', e => errors.push(e.message));
  const runs = (await (await page.request.get(`${base}/api/runs?page_size=100`)).json()).items;
  const done = runs.find(run => run.status === 'completed') || runs[0];
  const run = done && `/studio/runs/${encodeURIComponent(done.run_id)}`;

  // [文件名, 路径, 截图前的额外操作]
  const shots = [
    ['experiment-list', '/studio/Experiment'],
    ['experiment-cards', '/studio/Experiment', async () => {
      await page.getByRole('radio', { name: '卡片视图' }).click();
      await page.locator('.run-card').first().waitFor();
    }],
    ['experiment-new', '/studio/Experiment/new'],
    ...(run ? [
      ['run-progress', `${run}/progress`],
      ['run-agents', `${run}/agents`, async () => {
        await page.locator('.agent-table tbody tr').first().click();
        await page.locator('.agent-detail').waitFor();
      }],
      ['run-results', `${run}/results`, () => page.locator('canvas').waitFor()],
      ['run-logs', `${run}/logs`],
      ['run-config', `${run}/config`],
    ] : []),
    ['agent', '/studio/Agent', () => page.locator('.agent-table tbody tr').first().waitFor()],
    ['market', '/studio/Market', () => page.locator('canvas').waitFor()],
    ['setting', '/studio/Setting'],
    ['documentation', '/studio/Documentation', () => page.locator('.markdown').waitFor()],
  ];

  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const [name, path, prepare] of shots) {
      await page.goto(`${base}${path}`);
      // 每次从默认偏好开始，避免上一张截图切换的视图影响下一张。
      await page.evaluate(() => localStorage.clear());
      await page.reload();
      await page.locator('h1').first().waitFor();
      if (prepare) await prepare();
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${out}/${name}-${width}.png`, fullPage: true });
      console.log(`${out}/${name}-${width}.png`);
    }
  }
  if (errors.length) throw new Error(`页面运行错误：${errors.join('; ')}`);
} finally {
  await browser.close();
}
