// review-queue API thin wrapper.
//
// Why a separate file (not databaseApi.ts): the backend mounts review-queue
// at /api/review-queue while databaseApi.ts uses /api/database base URL.
// Mixing them would produce broken URLs. This wrapper uses utils/api.ts
// (base /api) to match the backend's actual mount path.
//
// Type definitions originally lived in ReviewQueuePage.tsx; they moved here
// when extracted ReviewQueueView and deleted ReviewQueuePage,
// so consumers (ReviewQueueView, ProjectDetail) share a single source of truth.

import { api } from '../utils/api';

export interface ReviewQueueItem {
  id: number;
  project_id: string;
  project_name: string;
  artifact_type: string;
  file_name: string;
  file_path: string;
  data_classification_flag: string;
  human_review_status: 'pending' | 'processing' | 'handled' | 'skipped';
  created_at: string;
  provenance?: Record<string, unknown>;
  // True iff the download stage already produced at least one non-failed
  // artifact for this paper (status completed/partial/awaiting_external).
  // Used to hide "Resume Download" when re-running can't improve the result.
  has_actionable_download?: boolean;
  // True iff the analysis artifact carries a provenance.cache_key — the
  // marker that an analysis_cache row with a usable plan_json exists.
  // Resume Download depends on this exact cache row, so the button must
  // stay hidden when it's missing (e.g. no_suitable_data papers, legacy
  // legacy artifacts).
  has_cached_plan?: boolean;
}

export interface ReviewQueueResponse {
  items: ReviewQueueItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface GetReviewQueueOptions {
  page?: number;
  pageSize?: number;
  status?: string;  // comma-separated, see backend allowlist
}

// Fetch a (possibly project-scoped) page of review-queue items.
// projectId optional — undefined preserves the cross-project behavior
// (kept as an escape hatch for a future global Dashboard).
export async function getReviewQueue(
  projectId: string | undefined,
  opts: GetReviewQueueOptions = {},
): Promise<ReviewQueueResponse> {
  const params = new URLSearchParams({
    page: String(opts.page ?? 1),
    page_size: String(opts.pageSize ?? 20),
  });
  if (opts.status) {
    params.set('status', opts.status);
  }
  if (projectId) {
    params.set('project_id', projectId);
  }
  return api.get<ReviewQueueResponse>(`/review-queue?${params.toString()}`);
}

// Pending-only count for the given project, used for tab badge / Overview CTA.
// Explicitly passes status=pending rather than relying on the backend's implicit
// default — keeps the count semantic stable if the backend default ever changes.
export async function getReviewQueueCount(
  projectId: string | undefined,
): Promise<number> {
  const resp = await getReviewQueue(projectId, { pageSize: 1, status: 'pending' });
  return resp.total ?? 0;
}
