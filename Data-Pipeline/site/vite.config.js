// Dev: /api goes to a local views service (python -m meridian.views ... --serve 8765) and /score to the risk
// service through a proxy, since the risk service's CORS list names the site's real origin only.
// Build: static files for `python -m meridian.views ... --serve <port> --site site/dist`.
import { defineConfig } from "vite";

const VIEWS = process.env.MERIDIAN_VIEWS || "http://127.0.0.1:8765";
const RISK = process.env.MERIDIAN_RISK || "https://meridian-risk-zyydw4crua-ue.a.run.app";

export default defineConfig({
  server: {
    proxy: {
      "/api": VIEWS,
      "/risk": { target: RISK, changeOrigin: true, rewrite: (p) => p.replace(/^\/risk/, "") },
    },
  },
  build: { outDir: "dist", assetsInlineLimit: 0 },
});
