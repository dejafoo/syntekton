import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  build: { outDir: "../src/product_factory/api/static/dashboard", emptyOutDir: true },
});
