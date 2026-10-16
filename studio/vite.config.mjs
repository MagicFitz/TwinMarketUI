import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: '/studio/',
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "127.0.0.1",
    // script/dev.sh 通过 TWINMARKET_API_URL 传入后端地址；单独运行 vite 时使用默认端口。
    proxy: { '/api': process.env.TWINMARKET_API_URL || 'http://127.0.0.1:8766' },
    allowedHosts: ["terminal.local"],
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  plugins: [react()],
});
