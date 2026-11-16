// 逐帧渲染 demo/promo/index.html，按页面时间线生成背景音乐，再编码为 MP4。
//   node demo/scripts/render-video.mjs [输出.mp4]          （默认 demo/out/twinmarketui-intro.mp4）
//   MUSIC=/path/to.wav node demo/scripts/render-video.mjs   # 用指定音乐；MUSIC= 置空则导出无声版本
//   STILLS=3,9.5,20 node demo/scripts/render-video.mjs      # 只导出这几秒的静帧，用于检查
import { chromium } from '../../frontend/node_modules/playwright/index.mjs';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const demo = new URL('../', import.meta.url).pathname;
const repo = new URL('../../', import.meta.url).pathname;
const pageUrl = new URL('../promo/index.html?export', import.meta.url).href;
const FPS = 30;
const framesDir = `${demo}.frames`;
const output = process.argv[2] || `${demo}out/twinmarketui-intro.mp4`;

const browser = await chromium.launch({ channel: 'chrome', args: ['--allow-file-access-from-files'] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
await page.goto(pageUrl);
await page.evaluate(() => window.ready);
const duration = await page.evaluate(() => window.DURATION);
const timeline = await page.evaluate(() => window.TIMELINE);

async function shoot(t, path) {
  await page.evaluate(t => window.render(t), t);
  await page.screenshot({ path, type: 'jpeg', quality: 95 });
}

if (process.env.STILLS) {
  const dir = `${demo}.stills`;
  await mkdir(dir, { recursive: true });
  for (const t of process.env.STILLS.split(',').map(Number)) {
    const path = `${dir}/t${t.toFixed(1)}.jpg`;
    await shoot(t, path);
    console.log(path);
  }
  await browser.close();
  process.exit(0);
}

await rm(framesDir, { recursive: true, force: true });
await mkdir(framesDir, { recursive: true });
const total = Math.round(duration * FPS);
for (let i = 0; i < total; i++) {
  await shoot(i / FPS, `${framesDir}/${String(i).padStart(5, '0')}.jpg`);
  if (i % 300 === 0) console.log(`frame ${i}/${total}`);
}
await browser.close();

// 背景音乐：默认按页面时间线现场合成（需要 backend/.venv 里的 numpy）
await mkdir(`${demo}out`, { recursive: true });
let music = process.env.MUSIC;
if (music === undefined) {
  const timelinePath = `${demo}out/timeline.json`;
  await writeFile(timelinePath, JSON.stringify(timeline, null, 2));
  music = `${demo}audio/bgm.wav`;
  const python = existsSync(`${repo}backend/.venv/bin/python`) ? `${repo}backend/.venv/bin/python` : 'python3';
  const m = spawnSync(python, [`${demo}scripts/make-music.py`, '--timeline', timelinePath, music], { stdio: 'inherit' });
  if (m.status !== 0) { console.log('背景音乐生成失败，导出无声版本'); music = ''; }
}
const hasMusic = Boolean(music) && existsSync(music);

const ffmpeg = process.env.FFMPEG || 'ffmpeg';
const r = spawnSync(ffmpeg, [
  '-y', '-loglevel', 'error', '-framerate', String(FPS), '-i', `${framesDir}/%05d.jpg`,
  ...(hasMusic ? ['-i', music] : []),
  // JPEG 帧是全范围色彩，转成标准的有限范围 yuv420p，避免部分播放器偏色
  '-vf', 'scale=in_range=pc:out_range=tv,format=yuv420p',
  '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
  ...(hasMusic ? ['-c:a', 'aac', '-b:a', '192k', '-shortest'] : []),
  '-movflags', '+faststart', output,
], { stdio: 'inherit' });
if (r.status !== 0) process.exit(r.status ?? 1);
await rm(framesDir, { recursive: true, force: true });
console.log(output);
