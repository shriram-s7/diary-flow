import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// DairyFlow uses its own ports so it can run next to other projects.
// Override with DAIRYFLOW_UI_PORT / DAIRYFLOW_API_PORT (the same variables the backend and start scripts read).
// The backend always listens on 127.0.0.1:<apiPort>; the browser only ever talks to this dev server.
const uiPort = Number(process.env.DAIRYFLOW_UI_PORT || 5600)
const apiPort = Number(process.env.DAIRYFLOW_API_PORT || 8600)
const backend = `http://127.0.0.1:${apiPort}`

const server = {
  host: true, // listen on all interfaces so a phone on the LAN can open the driver view
  port: uiPort,
  strictPort: true, // fail loudly instead of drifting to another port
  proxy: {
    // REST: /api/state -> backend /state (and /api/ws -> backend /ws, upgraded)
    '/api': {
      target: backend,
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
      ws: true,
    },
    // Live WebSocket under its own path as well
    '/ws': {
      target: backend,
      changeOrigin: true,
      ws: true,
    },
  },
}

export default defineConfig({
  plugins: [react()],
  server,
  preview: server,
  build: {
    rollupOptions: {
      input: {
        main: './index.html',
        driver: './driver.html',
      },
    },
  },
})
