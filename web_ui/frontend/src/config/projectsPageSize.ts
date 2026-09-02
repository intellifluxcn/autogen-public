const raw = import.meta.env.VITE_PROJECTS_PAGE_SIZE;
const parsed = parseInt(String(raw ?? ''), 10);
export const DEFAULT_PROJECTS_PAGE_SIZE =
  Number.isFinite(parsed) && parsed >= 1 ? Math.min(parsed, 500) : 10;
