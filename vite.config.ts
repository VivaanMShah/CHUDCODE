import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
 
// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // BACKEND PLACEHOLDER: when your API exists, proxy it here so the
    // frontend can call "/api/..." without CORS issues during development.
    // proxy: {
    //   "/api": {
    //     target: "http://localhost:3000",
    //     changeOrigin: true,
    //   },
    // },
  },
});