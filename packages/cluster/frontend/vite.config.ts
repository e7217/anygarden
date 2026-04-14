import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const apiPort = process.env.DEV_PORT || '8001'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  build: {
    outDir: '../doorae/static',
    emptyOutDir: true,
  },
  server: {
    host: true,
    proxy: {
      '/api': { target: `http://localhost:${apiPort}`, changeOrigin: true },
      '/ws': { target: `ws://localhost:${apiPort}`, ws: true },
    },
  },
})
