// Studio 端到端冒烟检查：针对 Radix Themes 界面，需先运行 script/dev.sh（后端 + Vite）。
//   STUDIO_URL=http://127.0.0.1:5173 npm run qa:smoke
// 只读：除显式验证的 POST 拒绝外，不向 /api 发送任何写请求。
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const base = process.env.STUDIO_URL || 'http://127.0.0.1:5173';
const out = 'qa/radix';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [], errors = [], writes = [];
const step = async (name, run) => { await run(); checks.push(name); console.log(`✓ ${name}`); };
const noOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => { if (r.url().includes('/api/') && r.method() !== 'GET') writes.push(`${r.method()} ${r.url()}`); });

  // 用真实数据挑选一个已完成实验作为工作台样本。
  const runs = (await (await page.request.get(`${base}/api/runs?page_size=100`)).json()).items;
  const done = runs.find(run => run.status === 'completed');
  assert.ok(done, '需要至少一个已完成的实验才能检查工作台');
  const runPath = `/studio/runs/${encodeURIComponent(done.run_id)}`;
  const rows = page.locator('.run-table tbody tr');

  await step('experiment list renders every run with created/updated columns', async () => {
    await page.goto(`${base}/studio/Experiment`);
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.getByRole('heading', { level: 1, name: '实验' }).waitFor();
    await rows.first().waitFor();
    assert.equal(await rows.count(), runs.length);
    for (const header of ['别名', '状态', '进度', '实验 ID', '创建时间', '更新时间', '操作'])
      await page.getByRole('columnheader', { name: header, exact: true }).waitFor();
    assert.equal(await page.getByRole('columnheader', { name: '市场快照' }).count(), 0);
    await page.getByRole('navigation', { name: '面包屑' }).getByText('实验').waitFor();
  });

  await step('column picker toggles columns, persists across reload and restores defaults', async () => {
    await page.getByRole('button', { name: '列设置' }).click();
    await page.getByRole('menuitemcheckbox', { name: '市场快照' }).click();
    await page.getByRole('menuitemcheckbox', { name: '更新时间' }).click();
    await page.keyboard.press('Escape');
    await page.getByRole('columnheader', { name: '市场快照' }).waitFor();
    assert.equal(await page.getByRole('columnheader', { name: '更新时间' }).count(), 0);
    await page.reload();
    await page.getByRole('columnheader', { name: '市场快照' }).waitFor();
    assert.equal(await page.getByRole('columnheader', { name: '更新时间' }).count(), 0);
    await page.getByRole('button', { name: '列设置' }).click();
    await page.getByRole('menuitem', { name: '恢复默认' }).click();
    await page.getByRole('columnheader', { name: '更新时间' }).waitFor();
    assert.equal(await page.getByRole('columnheader', { name: '市场快照' }).count(), 0);
  });

  await step('table/card toggle shows the same runs and persists', async () => {
    await page.getByRole('radio', { name: '卡片视图' }).click();
    await page.locator('.run-card').first().waitFor();
    assert.equal(await page.locator('.run-card').count(), runs.length);
    assert.equal(await page.getByRole('button', { name: '列设置' }).count(), 0, '卡片视图不显示列设置');
    await page.reload();
    await page.locator('.run-card').first().waitFor();
    await page.getByRole('radio', { name: '列表视图' }).click();
    await rows.first().waitFor();
  });

  await step('search and status filter narrow the list', async () => {
    await page.getByRole('textbox', { name: '搜索实验' }).fill('NO_SUCH_EXPERIMENT');
    await page.getByText('没有符合筛选条件的实验。').waitFor();
    await page.getByRole('textbox', { name: '搜索实验' }).fill('');
    await page.getByRole('combobox', { name: '按状态筛选' }).click();
    await page.getByRole('option', { name: '已完成' }).click();
    const completed = runs.filter(run => run.status === 'completed').length;
    await page.waitForFunction(count => document.querySelectorAll('.run-table tbody tr').length === count, completed);
    for (const text of await rows.locator('td:nth-child(2)').allTextContents()) assert.equal(text, '已完成');
    await page.getByRole('combobox', { name: '按状态筛选' }).click();
    await page.getByRole('option', { name: '全部状态' }).click();
    await page.waitForFunction(count => document.querySelectorAll('.run-table tbody tr').length === count, runs.length);
  });

  await step('new experiment page: breadcrumb, cancel returns to origin, deep link falls back to list', async () => {
    await page.getByRole('button', { name: '创建实验' }).click();
    await page.waitForURL('**/studio/Experiment/new');
    await page.getByRole('heading', { level: 1, name: '新建实验' }).waitFor();
    // 默认别名为“实验 N”，N 不小于已有实验数 + 1。
    await page.waitForFunction(() => /^实验 \d+$/.test(document.querySelector('input[required]')?.value || ''));
    const suggested = Number((await page.getByLabel('实验别名').inputValue()).split(' ')[1]);
    assert.ok(suggested > runs.length, `默认序号 ${suggested} 应大于已有实验数 ${runs.length}`);
    const crumbs = page.getByRole('navigation', { name: '面包屑' });
    await crumbs.getByRole('link', { name: '实验' }).waitFor();
    await crumbs.getByText('新建实验').waitFor();
    await page.getByRole('button', { name: '取消' }).click();
    await page.waitForURL(url => url.pathname === '/studio/Experiment');
    // 直接打开创建页时没有站内历史，取消应回到列表而不是离开站点。
    await page.goto(`${base}/studio/Experiment/new`);
    await page.getByRole('heading', { level: 1, name: '新建实验' }).waitFor();
    await page.getByRole('button', { name: '取消' }).click();
    await page.waitForURL(url => url.pathname === '/studio/Experiment');
    await rows.first().waitFor();
  });

  await step('unsaved edits prompt before leaving; declining stays, accepting navigates', async () => {
    await page.goto(`${base}/studio/Experiment/new?from=${encodeURIComponent(done.run_id)}`);
    const name = page.getByLabel('实验别名');
    await page.waitForFunction(expected => document.querySelector('input[required]')?.value === expected, `${done.name} 副本`);
    assert.equal(await name.inputValue(), `${done.name} 副本`, '复制实验应预填源实验配置');
    // 未修改时离开不提示。
    let prompts = 0;
    const answer = accept => dialog => { prompts += 1; assert.match(dialog.message(), /未保存/); return accept ? dialog.accept() : dialog.dismiss(); };
    await name.fill('冒烟测试草稿');
    let handler = answer(false);
    page.on('dialog', handler);
    await page.locator('.sidebar a[href="/studio/Market"]').click();
    await page.waitForTimeout(300);
    assert.equal(prompts, 1);
    assert.match(page.url(), /\/studio\/Experiment\/new/);
    page.off('dialog', handler);
    handler = answer(true);
    page.on('dialog', handler);
    await page.locator('.sidebar a[href="/studio/Market"]').click();
    await page.waitForURL('**/studio/Market');
    assert.equal(prompts, 2);
    page.off('dialog', handler);
  });

  await step('workspace opens completed run on results, tabs are URL-addressable', async () => {
    await page.goto(`${base}${runPath}`);
    await page.getByRole('heading', { level: 1, name: done.name }).waitFor();
    assert.equal(await page.getByRole('tab', { name: '结果' }).getAttribute('aria-selected'), 'true');
    await page.getByRole('navigation', { name: '面包屑' }).getByText(done.name).waitFor();
    await page.getByRole('heading', { name: '结果数据表' }).waitFor();
    for (const [tab, segment, heading] of [['运行进度', 'progress', '当日流程'], ['日志与诊断', 'logs', '运行日志'], ['配置', 'config', '实验参数']]) {
      await page.getByRole('tab', { name: tab }).click();
      await page.waitForURL(`**${runPath}/${segment}`);
      await page.getByRole('heading', { name: heading }).waitFor();
    }
    await page.reload();
    await page.getByRole('heading', { name: '实验参数' }).waitFor();
    // 切换 tab 使用 replace，返回键直接回到上一页面而不是逐个 tab 后退。
    await page.goBack();
    assert.doesNotMatch(page.url(), /\/studio\/runs\//);
  });

  await step('workspace Agents tab: side panel with pipeline, LLM context and profile', async () => {
    await page.goto(`${base}${runPath}/agents`);
    const agentRows = page.locator('.agent-table tbody tr');
    await agentRows.first().waitFor();
    const id = (await agentRows.first().locator('th').innerText()).split('\n')[0].trim();
    await agentRows.first().click();
    await page.waitForURL(url => url.searchParams.get('agent') === id);
    const detail = page.locator('.agent-detail');
    await detail.getByRole('heading', { name: `Agent ${id}` }).waitFor();
    for (const tab of ['决策流水线', 'LLM 上下文', '档案与持仓']) await detail.getByRole('tab', { name: tab }).waitFor();
    await detail.getByText('读取状态').waitFor();
    await detail.getByRole('tab', { name: 'LLM 上下文' }).click();
    await detail.getByText(/LLM 调用|还没有 LLM 调用记录/).first().waitFor();
    await detail.getByRole('tab', { name: '档案与持仓' }).click();
    await detail.getByText(/当前持仓|暂无档案快照/).first().waitFor();
    await page.screenshot({ path: `${out}/smoke-agent-detail.png` });
    await page.getByRole('button', { name: '关闭详情' }).click();
    await page.waitForURL(url => !url.searchParams.has('agent'));
    assert.equal(await detail.count(), 0);
  });

  await step('Agent space reuses the explorer: detail, pagination, search', async () => {
    await page.goto(`${base}/studio/Agent`);
    const agentRows = page.locator('.agent-table tbody tr');
    await agentRows.first().waitFor();
    assert.equal(await page.getByRole('radio', { name: /运行状态/ }).count(), 0, '模板没有运行时数据源');
    await agentRows.first().click();
    const detail = page.locator('.agent-detail');
    await detail.getByRole('heading', { name: '当前持仓' }).waitFor();
    assert.equal(await detail.getByRole('tab').count(), 1);
    await page.getByRole('button', { name: '关闭详情' }).click();
    const first = await agentRows.first().locator('th').innerText();
    await page.getByRole('button', { name: '下一页' }).click();
    await page.waitForFunction(old => document.querySelector('.agent-table tbody tr th')?.innerText !== old, first);
    await page.getByRole('textbox', { name: '搜索成员' }).fill('NO_SUCH_MEMBER');
    await page.getByText('没有匹配的成员').waitFor();
  });

  await step('market chart/table toggle and asset select', async () => {
    await page.goto(`${base}/studio/Market`);
    await page.locator('canvas').waitFor();
    await page.getByRole('radio', { name: '数据' }).click();
    assert.ok(await page.locator('.scroll-table tbody tr').count() > 10);
    await page.getByRole('radio', { name: '曲线' }).click();
    await page.locator('canvas').waitFor();
    await page.getByRole('combobox', { name: '选择资产' }).click();
    await page.getByRole('option').filter({ hasText: 'TTEI' }).click();
    assert.match(await page.getByRole('combobox', { name: '选择资产' }).textContent(), /TTEI/);
  });

  await step('documentation: search, internal links, reload/back, traversal rejected', async () => {
    await page.goto(`${base}/studio/Documentation`);
    await page.locator('.markdown').waitFor();
    const total = await page.locator('.docs-index nav a').count();
    assert.ok(total >= 1);
    await page.getByRole('textbox', { name: '搜索文档' }).fill('NO_SUCH_DOC');
    assert.equal(await page.locator('.docs-index nav a').count(), 0);
    await page.getByRole('textbox', { name: '搜索文档' }).fill('');
    assert.equal(await page.locator('.docs-index nav a').count(), total);
    if (total > 1) {
      await page.locator('.docs-index nav a').nth(1).click();
      await page.waitForURL('**doc=*');
      const title = await page.locator('.doc-heading h2').innerText();
      await page.reload();
      await page.locator('.doc-heading h2', { hasText: title }).waitFor();
    }
    assert.equal((await page.request.get(`${base}/api/documentation?path=../config/api.yaml`)).status(), 404);
  });

  await step('unsafe Markdown is isolated and revoked documents disappear', async () => {
    let revoked = false;
    const doc = { path: 'fixture.md', title: '安全测试', category: '测试', content: '# 安全测试\n\n<script>window.__unsafe=1</script>\n\n<img src="https://example.invalid/track" onerror="window.__unsafe=1"/>\n\n![Blocked](https://example.invalid/track)\n\n[bad](javascript:alert(1))' };
    const outbound = [];
    page.on('request', r => { if (r.url().includes('example.invalid')) outbound.push(r.url()); });
    await page.route('**/api/documentation**', route => {
      if (new URL(route.request().url()).searchParams.has('path')) return route.fulfill({ status: revoked ? 404 : 200, json: revoked ? { error: '未发布' } : doc });
      return route.fulfill({ json: { items: revoked ? [] : [{ path: doc.path, title: doc.title, category: doc.category }] } });
    });
    await page.goto(`${base}/studio/Documentation?doc=fixture.md`);
    await page.getByRole('heading', { name: '安全测试' }).waitFor();
    assert.equal(await page.evaluate(() => window.__unsafe), undefined);
    assert.equal(await page.locator('.markdown script,.markdown img,.markdown a[href^="javascript:"]').count(), 0);
    assert.deepEqual(outbound, []);
    revoked = true;
    await page.getByRole('status').filter({ hasText: '文档未发布或已撤回' }).waitFor({ timeout: 12000 });
    assert.equal(await page.locator('.markdown').count(), 0);
    await page.unroute('**/api/documentation**');
  });

  await step('list keeps last data while the API is down and recovers', async () => {
    await page.goto(`${base}/studio/Experiment`);
    await rows.first().waitFor();
    await page.route('**/api/runs?**', route => route.abort());
    await page.getByRole('button', { name: '立即刷新' }).click();
    await page.getByRole('alert').filter({ hasText: '显示上次读取的数据' }).waitFor();
    assert.equal(await rows.count(), runs.length);
    await page.unroute('**/api/runs?**');
    await page.getByRole('button', { name: '立即刷新' }).click();
    await page.getByRole('alert').filter({ hasText: '显示上次读取的数据' }).waitFor({ state: 'hidden' });
  });

  await step('no horizontal overflow at 1440/1024/390; mobile uses bottom navigation', async () => {
    const routes = ['/studio/Experiment', '/studio/Experiment/new', `${runPath}/progress`, `${runPath}/agents`, '/studio/Agent', '/studio/Market', '/studio/Setting', '/studio/Documentation'];
    for (const width of [1440, 1024, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const route of routes) {
        await page.goto(`${base}${route}`);
        await page.locator('h1').first().waitFor();
        await page.waitForTimeout(300);
        assert.ok(await noOverflow(page), `${route} overflows at ${width}px`);
      }
      if (width === 390) {
        const nav = await page.locator('.sidebar').boundingBox();
        assert.ok(Math.abs(nav.y + nav.height - 900) <= 1, '移动端导航固定在底部');
        assert.equal(await page.locator('.brand').isVisible(), false);
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  });

  await step('no runtime errors, no write requests, POST to docs rejected', async () => {
    assert.deepEqual(errors, []);
    assert.deepEqual(writes, []);
    assert.equal((await page.request.post(`${base}/api/documentation`, { data: {} })).status(), 405);
  });

  console.log(JSON.stringify({ passed: true, checks: checks.length }, null, 2));
} finally {
  await browser.close();
}
