/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const host = process.env.TAURI_DEV_HOST;

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react()],

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
  build: {
    rollupOptions: {
      output: {
        // src/i18n/index.ts imports en.json and zh.json statically. Putting them in
        // their own chunk keeps the main index-*.js chunk to code, so its size cap in
        // scripts/check-bundle-size.mjs measures code; the locales chunk is still a
        // static dependency, so index.html modulepreloads it and the first render
        // already has the user's language (no flash of English).
        manualChunks(id: string) {
          if (/[\\/]i18n[\\/]locales[\\/]/.test(id)) return "locales";
          return undefined;
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts,jsx,tsx}'],
    setupFiles: ['./src/test-setup.ts'],
    testTimeout: 15_000,  // PBT tests need headroom under full-suite concurrency
  },
}));
