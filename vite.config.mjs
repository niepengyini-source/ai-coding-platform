import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  define: {'process.env.NODE_ENV': JSON.stringify('production')},
  build: {
    outDir: 'static/workbench',
    emptyOutDir: false,
    lib: {
      entry: 'frontend/task-queue.js',
      name: 'WorkbenchTasks',
      formats: ['iife'],
      fileName: () => 'task-queue.js',
    },
  },
});
