import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
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
    outDir: 'dist',
    rollupOptions: {
      output: {
        manualChunks: {
          'monaco': ['@monaco-editor/react'],
          'ag-grid': ['ag-grid-community', 'ag-grid-react'],
          'vendor': ['react', 'react-dom'],
          'query': ['@tanstack/react-query', '@tanstack/react-virtual', 'zustand'],
        },
      },
    },
  },
})
