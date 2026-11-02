import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// script/dev.sh 通过 TWINMARKET_API_URL 传入后端地址；也兼容 VITE_DEV_API_TARGET；单独运行 vite 时使用默认端口。
const apiTarget = process.env.TWINMARKET_API_URL || process.env.VITE_DEV_API_TARGET || 'http://127.0.0.1:8766';
// 市场数据服务独立运行；浏览器经 /mds 代理访问，令牌只在代理里附加，不进入前端代码。
const mdsTarget = process.env.TWINMARKET_MDS_URL || 'http://127.0.0.1:8100';
const mdsToken = process.env.TWINMARKET_MDS_TOKEN || '';

export default defineConfig({
  base: '/studio/',
  // 侧栏显示后端地址并链接到它的 /docs 调试页。
  define: { __API_TARGET__: JSON.stringify(apiTarget) },
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "127.0.0.1",
    proxy: {
      '/api': apiTarget,
      '/mds': {
        target: mdsTarget,
        rewrite: (path) => path.replace(/^\/mds/, ''),
        headers: mdsToken ? { Authorization: `Bearer ${mdsToken}` } : {},
      },
    },
    allowedHosts: ["terminal.local"],
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  plugins: [react()],
});
