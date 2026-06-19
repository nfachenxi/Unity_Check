import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import ElementPlus from 'unplugin-element-plus/vite'

export default defineConfig({
  plugins: [
    vue(),
    ElementPlus({ useSource: true }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'es2020',
    cssMinify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks(id) {
          // Vendor chunk splitting for browser caching optimization
          if (id.includes('node_modules/vue')) return 'vendor-vue'
          if (id.includes('node_modules/element-plus') || id.includes('node_modules/@element-plus')) return 'vendor-element'
          if (id.includes('node_modules/echarts') || id.includes('node_modules/vue-echarts')) return 'vendor-echarts'
          if (id.includes('node_modules/axios')) return 'vendor-axios'
        },
      },
    },
  },
})
