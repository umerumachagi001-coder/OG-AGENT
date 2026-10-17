import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built files are served by the orchestrator at http://localhost:8001/ui (see orchestrator/app.py),
// so `make demo` is one command. `npm run dev` gives hot reload on :5173 for P3.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
});
