/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '::1']);

// Configuration is read from the repository root .env (envDir: '..').
// Only VITE_* variables are exposed to browser code; they contain no secrets.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', '');
  const host = env.FRONTEND_HOST || '127.0.0.1';
  if (!LOOPBACK.has(host)) {
    throw new Error(`FRONTEND_HOST=${host} izin verilmiyor: arayüz yalnızca 127.0.0.1/localhost üzerinde çalışır.`);
  }
  const port = Number(env.FRONTEND_PORT || 5173);
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000';
  const targetHost = new URL(apiTarget).hostname;
  if (!LOOPBACK.has(targetHost)) {
    throw new Error('VITE_API_PROXY_TARGET yalnızca yerel FastAPI adresini gösterebilir.');
  }
  // Lets the local Docker Playwright browser reach the app for testing.
  const allowedHosts = ['host.docker.internal'];
  const proxy = { '/api': { target: apiTarget, changeOrigin: false, ws: false } };
  const securityHeaders = {
    'Content-Security-Policy':
      "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; " +
      "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
  };
  return {
    envDir: '..',
    plugins: [react()],
    server: { host, port, strictPort: true, allowedHosts, proxy },
    preview: { host, port, strictPort: true, allowedHosts, proxy, headers: securityHeaders },
    build: { outDir: 'dist', sourcemap: false },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      css: false,
      // threads + generous timeouts keep the suite reliable while the local
      // backend is busy with OCR/LLM work on the same CPU
      pool: 'threads',
      testTimeout: 30000,
    },
  };
});
