import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

/**
 * The dev server proxies `/v1` to the API on the same origin as the app.
 *
 * Same-origin rather than CORS on purpose. The session cookie is `SameSite=Lax` and `HttpOnly`; a
 * cross-origin front end would need `SameSite=None`, which is exactly the setting that makes a
 * cookie available to a cross-site request. Proxying keeps the browser's own protection in force
 * during development and matches how the application is served in a deployment.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: process.env.ACCESSFORGE_API_ORIGIN ?? 'http://127.0.0.1:8080', changeOrigin: false },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
})
