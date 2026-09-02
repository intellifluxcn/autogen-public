// Shared cache for /api/models — the OpenRouter model list plus env-resolved
// per-stage defaults. Hit by both the New-Project form (ProjectManager) and
// the project detail page (ProjectDetail) to show "Default (<model>)" hints.
// Cached for 24h since the underlying values (env vars + OpenRouter catalog)
// rarely change; the server already proxies with a 1h memory cache.

import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../utils/api';

export type ModelOption = { id: string; name: string };
export type ModelDefaults = { analysis?: string; download?: string };
export type ModelsResponse = {
  models: ModelOption[];
  defaults?: ModelDefaults;
};

const MODELS_QK = 'models';
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export function useModels(options: { enabled?: boolean } = {}) {
  return useQuery<ModelsResponse>({
    queryKey: [MODELS_QK],
    queryFn: () => apiClient.get<ModelsResponse>('/models'),
    staleTime: ONE_DAY_MS,
    gcTime: ONE_DAY_MS,
    refetchOnMount: false,
    refetchOnReconnect: false,
    enabled: options.enabled ?? true,
  });
}
