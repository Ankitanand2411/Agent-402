import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    exclude: ['onnxruntime-node', '@xenova/transformers', 'agentic-flow', 'better-sqlite3']
  },
  build: {
    rollupOptions: {
      external: ['onnxruntime-node', 'better-sqlite3', 'agentic-flow']
    }
  }
})
