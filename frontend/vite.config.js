import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import path from 'path'

export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
    }),
    Components({
      resolvers: [ElementPlusResolver()],
    }),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: process.env.DESKTOP_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,  // 同时代理 WebSocket（/api/training/ws/...）
      },

    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          charts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers', 'vue-echarts'],
          vue: ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
})
