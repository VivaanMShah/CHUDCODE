import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In development the frontend calls "/api/..." and Vite forwards it to the
    // FastAPI backend (uvicorn app.main:app --port 8000), so there are no CORS
    // issues. In production, set VITE_API_URL to the deployed API instead.
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
