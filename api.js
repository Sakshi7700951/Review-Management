// Single source of truth for the backend URL.
// Vite picks .env.development for `npm run dev` and .env.production for `npm run build`.
// Default to `/api` so frontend works when backend is served under the same origin.
// Override via `VITE_API_BASE` in your environment when needed (e.g., remote backend).
// Local development fallback when Vite env variables are not loaded.
// Production should still set VITE_API_BASE explicitly.
export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:2034/api";
