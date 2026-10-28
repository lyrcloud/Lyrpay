import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Same paths as the deployed site, where CloudFront sends /api/* to the API Lambda
    proxy: { '/api': 'http://localhost:8000' },
  },
});
