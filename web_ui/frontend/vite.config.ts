import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const BUILD_TIME = new Date().toISOString()

// Pull semver from the repo's pyproject.toml (single source of truth for the
// whole app) and short git SHA at build time. Both fall back to safe defaults
// when run from an environment without git / outside the repo.
function readAppVersion(): string {
  try {
    const text = readFileSync(resolve(__dirname, '../../pyproject.toml'), 'utf-8')
    const m = text.match(/^version\s*=\s*"([^"]+)"/m)
    return m ? m[1] : '0.0.0'
  } catch {
    return '0.0.0'
  }
}

function readGitSha(): string {
  try {
    return execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim()
  } catch {
    return 'unknown'
  }
}

const APP_VERSION = readAppVersion()
const APP_BUILD_SHA = readGitSha()

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_BUILD_TIME__: JSON.stringify(BUILD_TIME),
    __APP_VERSION__: JSON.stringify(APP_VERSION),
    __APP_BUILD_SHA__: JSON.stringify(APP_BUILD_SHA),
  },
  server: {
    port: 3000,
    proxy: {
      '/socket.io': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          // Stable React runtime — cached across deploys
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
          // Data-fetching layer
          'vendor-query': ['@tanstack/react-query'],
          // WebSocket client — large but always needed after login
          'vendor-socket': ['socket.io-client'],
          // Markdown renderer — only loaded on file preview
          'vendor-markdown': ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
})
