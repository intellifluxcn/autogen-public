// extracted from the deleted ReviewQueuePage so the same
// list/actions/pagination can render inside ProjectDetail's Review tab.
//
// Behavior preserved from the earlier version:
//   - status filter dropdown (pending | processing | handled | skipped),
//     resetting page to 1 on change so pagination never goes out of bounds;
//   - PATCH /api/artifacts/{id}/review-status for handled/skipped/processing
//     transitions, POST /api/review-queue/{id}/resume-download for resume;
//   - i18n keys reused verbatim from the earlier version (review_queue.*).
//
// New:
//   - projectId prop: when defined, query is project-scoped (passes
//     ?project_id=...) and the "Project" column + ExternalLink icon
//     are hidden (cross-project Link would be self-referential in-tab);
//   - onCountChange prop: fires on mount and after each successful action
//     with the pending-only count, sourced from a side-channel
//     getReviewQueueCount call (not from the visible filter's total).

import React, { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import { Link } from 'react-router-dom';
import {
  Mail,
  RefreshCw,
  CheckCircle,
  XCircle,
  ExternalLink,
  Info,
  ChevronDown,
  ChevronUp,
  Clock,
  RotateCcw,
  Wrench,
} from 'lucide-react';
import { useTranslation } from '../hooks/useTranslation';
import { api } from '../utils/api';
import {
  getReviewQueue,
  getReviewQueueCount,
  ReviewQueueItem,
} from '../services/reviewQueueApi';

// Lazy: keeps react-markdown out of the Review tab's initial chunk; same
// pattern as ArtifactList. Loaded only when an operator clicks a row.
const FileViewerModal = lazy(() =>
  import('./FileViewerModal').then(m => ({ default: m.FileViewerModal }))
);

const STATUS_OPTIONS = ['pending', 'processing', 'handled', 'skipped'] as const;

interface ReviewQueueViewProps {
  // When defined: query is filtered to this project, and project-context
  // affordances (Project column, ExternalLink icon) are suppressed.
  projectId?: string;
  // Notified on mount and after every status mutation with the pending-only
  // count — independent of the currently-visible status filter.
  onCountChange?: (pendingCount: number) => void;
}

export const ReviewQueueView: React.FC<ReviewQueueViewProps> = ({
  projectId,
  onCountChange,
}) => {
  const { t } = useTranslation();
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<string>('pending');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyArtifactId, setBusyArtifactId] = useState<number | null>(null);
  const [infoOpen, setInfoOpen] = useState(true);
  const [previewArtifact, setPreviewArtifact] = useState<{ id: number; name: string } | null>(null);

  const pageSize = 20;
  const isInProject = projectId !== undefined;

  // Pending count is the authoritative source for tab badge / Overview CTA.
  // When the user's filter is something OTHER than `pending` (e.g. they're
  // viewing "handled"), the list's `total` reflects that filter, so we need
  // a separate page_size=1 call to keep the badge honest. But when the
  // filter IS `pending` (the default on tab entry), the list response
  // already carries the pending count — so reuse it and skip the extra
  // round-trip. Most users never change the filter, so this dedup
  // eliminates one network call on the common path.
  const reportPendingCount = useCallback(async () => {
    if (!onCountChange) return;
    // Skip — loadQueue is already going to report the count via the same
    // total field on the page_size=20 response.
    if (statusFilter === 'pending') return;
    try {
      const n = await getReviewQueueCount(projectId);
      onCountChange(n);
    } catch {
      // Count is best-effort; failure shouldn't block the view.
    }
  }, [projectId, onCountChange, statusFilter]);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getReviewQueue(projectId, {
        page,
        pageSize,
        status: statusFilter,
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
      // Fast-path the pending-count update when the user's filter coincides
      // with what the badge wants. Mirrors the dedup logic in
      // reportPendingCount above.
      if (statusFilter === 'pending' && onCountChange) {
        onCountChange(data.total || 0);
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to load review queue';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [projectId, page, statusFilter, onCountChange]);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  // Mount-time + projectId-change pending-count refresh, independent of the
  // visible page/filter so the parent sees a stable pending number.
  useEffect(() => {
    reportPendingCount();
  }, [reportPendingCount]);

  const updateStatus = async (
    artifactId: number,
    newStatus: 'handled' | 'skipped' | 'processing' | 'pending',
  ) => {
    setBusyArtifactId(artifactId);
    try {
      await api.patch(`/artifacts/${artifactId}/review-status`, { status: newStatus });
      await loadQueue();
      await reportPendingCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Status update failed';
      setError(msg);
    } finally {
      setBusyArtifactId(null);
    }
  };

  const resumeDownload = async (artifactId: number) => {
    setBusyArtifactId(artifactId);
    try {
      await api.post(`/review-queue/${artifactId}/resume-download`, {});
      await loadQueue();
      await reportPendingCount();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Resume failed';
      setError(msg);
    } finally {
      setBusyArtifactId(null);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div>
      {/* Collapsible help panel. Default open so first-time users see the
          context; remembers state for the session via useState only (not
          persisted, intentional — gentle nudge that this is help text). */}
      <div className="mb-4 bg-blue-50 border border-blue-200 rounded-lg">
        <button
          type="button"
          onClick={() => setInfoOpen((o) => !o)}
          className="w-full px-4 py-2 flex items-center justify-between text-sm font-medium text-blue-900 hover:bg-blue-100/50 rounded-lg"
        >
          <span className="flex items-center gap-2">
            <Info className="w-4 h-4" />
            {infoOpen ? t('review_queue.info_toggle_close') : t('review_queue.info_toggle_open')}
          </span>
          {infoOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
        {infoOpen && (
          <div className="px-4 pb-4 space-y-4 text-sm text-blue-900/90 border-t border-blue-200 pt-3">
            <p>{t('review_queue.info_intro')}</p>
            <div>
              <h4 className="font-semibold mb-2">{t('review_queue.info_section_flags')}</h4>
              <ul className="space-y-1.5 ml-1">
                <li className="flex items-start gap-2">
                  <Mail className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-700" />
                  <span><strong>{t('review_queue.flag_contact_author')}</strong>: {t('review_queue.info_flag_contact_author')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Wrench className="w-4 h-4 mt-0.5 flex-shrink-0 text-orange-700" />
                  <span><strong>{t('review_queue.flag_manual_required')}</strong>: {t('review_queue.info_flag_manual_required')}</span>
                </li>
              </ul>
            </div>
            <div>
              <h4 className="font-semibold mb-2">{t('review_queue.info_section_actions')}</h4>
              <ul className="space-y-1.5 ml-1">
                <li className="flex items-start gap-2">
                  <RefreshCw className="w-4 h-4 mt-0.5 flex-shrink-0 text-blue-700" />
                  <span><strong>{t('review_queue.action_resume_download')}</strong>: {t('review_queue.info_action_resume')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <Clock className="w-4 h-4 mt-0.5 flex-shrink-0 text-amber-700" />
                  <span><strong>{t('review_queue.action_in_progress')}</strong>: {t('review_queue.info_action_in_progress')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0 text-gray-600" />
                  <span><strong>{t('review_queue.action_skip')}</strong>: {t('review_queue.info_action_skip')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <CheckCircle className="w-4 h-4 mt-0.5 flex-shrink-0 text-green-700" />
                  <span><strong>{t('review_queue.action_mark_handled')}</strong>: {t('review_queue.info_action_handled')}</span>
                </li>
                <li className="flex items-start gap-2">
                  <RotateCcw className="w-4 h-4 mt-0.5 flex-shrink-0 text-gray-600" />
                  <span><strong>{t('review_queue.action_return_to_pending')}</strong>: {t('review_queue.info_action_return')}</span>
                </li>
              </ul>
            </div>
          </div>
        )}
      </div>

      <div className="mb-4 flex items-center gap-2 flex-wrap">
        <span className="text-sm text-gray-600">{t('review_queue.filter_status')}:</span>
        {/* Inline pill buttons — replaces the old <select> dropdown. With
            only 4 options the dropdown's two-click "open → pick" cost is
            higher than the screen real estate four buttons cost; the
            active button also acts as a visual indicator of which list
            the user is looking at. */}
        <div className="inline-flex gap-1">
          {STATUS_OPTIONS.map(s => {
            const isActive = statusFilter === s;
            return (
              <button
                key={s}
                type="button"
                onClick={() => { setPage(1); setStatusFilter(s); }}
                className={
                  'text-xs px-3 py-1.5 rounded border transition-colors ' +
                  (isActive
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-700 border-gray-300 hover:bg-gray-50')
                }
              >
                {t(`review_queue.status_${s}`)}
              </button>
            );
          })}
        </div>
        <span className="text-xs text-gray-500 ml-3">
          {total} {t('review_queue.total_items')}
        </span>
        <button
          type="button"
          onClick={loadQueue}
          className="flex items-center gap-1 text-sm px-3 py-1.5 border rounded hover:bg-gray-50 ml-auto"
        >
          <RefreshCw className="w-4 h-4" />
          {t('review_queue.refresh')}
        </button>
      </div>

      {loading && <p className="text-sm text-gray-500">{t('common.loading')}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}

      {!loading && !error && items.length === 0 && (
        <p className="text-sm text-gray-500 py-8 text-center">
          {t('review_queue.empty_state')}
        </p>
      )}

      {!loading && items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-3 py-2">{t('review_queue.column_paper')}</th>
                {!isInProject && (
                  <th className="text-left px-3 py-2">{t('review_queue.column_project')}</th>
                )}
                <th className="text-left px-3 py-2">{t('review_queue.column_classification')}</th>
                <th className="text-left px-3 py-2">{t('review_queue.column_status')}</th>
                <th className="text-left px-3 py-2">{t('review_queue.column_actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.id} className="border-b hover:bg-gray-50">
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() =>
                          setPreviewArtifact({
                            id: item.id,
                            name: item.file_name || `artifact ${item.id}`,
                          })
                        }
                        title={t('review_queue.preview_markdown')}
                        className="text-blue-600 hover:underline text-left"
                      >
                        {item.file_name || `artifact ${item.id}`}
                      </button>
                      {!isInProject && (
                        // Cross-project mode keeps the project-navigation
                        // affordance separate from the markdown preview so
                        // each click target has one job.
                        <Link
                          to={`/project/${item.project_id}`}
                          title={t('review_queue.open_project')}
                          className="text-gray-500 hover:text-gray-700"
                        >
                          <ExternalLink className="w-3 h-3" />
                        </Link>
                      )}
                    </div>
                  </td>
                  {!isInProject && (
                    <td className="px-3 py-2 text-gray-700">{item.project_name}</td>
                  )}
                  <td className="px-3 py-2">
                    <span className={
                      item.data_classification_flag === 'contact_author'
                        ? 'inline-block px-2 py-0.5 text-xs rounded bg-amber-100 text-amber-800'
                        : 'inline-block px-2 py-0.5 text-xs rounded bg-orange-100 text-orange-800'
                    }>
                      {t(`review_queue.flag_${item.data_classification_flag}`)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <span className="text-xs text-gray-600">
                      {t(`review_queue.status_${item.human_review_status}`)}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {item.human_review_status === 'pending' && (
                        <>
                          {/* Resume Download is only shown when BOTH:
                              1. The previous download genuinely failed or
                                 never ran (no completed/partial/awaiting_external
                                 artifact yet) — otherwise re-running just
                                 reproduces the existing partial result.
                              2. The artifact carries a cached download plan
                                 (provenance.cache_key) — without it the
                                 backend resume endpoint 400s with "Legacy
                                 artifact has no cache_key". Most commonly
                                 missing on no_suitable_data papers where the
                                 LLM concluded there is no actionable plan. */}
                          {!item.has_actionable_download && item.has_cached_plan && (
                            <button
                              type="button"
                              onClick={() => resumeDownload(item.id)}
                              disabled={busyArtifactId === item.id}
                              title={t('review_queue.info_action_resume')}
                              className="inline-flex items-center gap-1 px-2 py-1 text-xs text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded disabled:opacity-50"
                            >
                              <RefreshCw className="w-3.5 h-3.5" />
                              <span>{t('review_queue.action_resume_download')}</span>
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => updateStatus(item.id, 'processing')}
                            disabled={busyArtifactId === item.id}
                            title={t('review_queue.info_action_in_progress')}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-amber-700 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded disabled:opacity-50"
                          >
                            <Clock className="w-3.5 h-3.5" />
                            <span>{t('review_queue.action_in_progress')}</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => updateStatus(item.id, 'skipped')}
                            disabled={busyArtifactId === item.id}
                            title={t('review_queue.info_action_skip')}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-600 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded disabled:opacity-50"
                          >
                            <XCircle className="w-3.5 h-3.5" />
                            <span>{t('review_queue.action_skip')}</span>
                          </button>
                        </>
                      )}
                      {item.human_review_status === 'processing' && (
                        <>
                          <button
                            type="button"
                            onClick={() => updateStatus(item.id, 'handled')}
                            disabled={busyArtifactId === item.id}
                            title={t('review_queue.info_action_handled')}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-green-700 bg-green-50 hover:bg-green-100 border border-green-200 rounded disabled:opacity-50"
                          >
                            <CheckCircle className="w-3.5 h-3.5" />
                            <span>{t('review_queue.action_mark_handled')}</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => updateStatus(item.id, 'pending')}
                            disabled={busyArtifactId === item.id}
                            title={t('review_queue.info_action_return')}
                            className="inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-600 bg-gray-50 hover:bg-gray-100 border border-gray-200 rounded disabled:opacity-50"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                            <span>{t('review_queue.action_return_to_pending')}</span>
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage(p => Math.max(1, p - 1))}
            className="text-sm px-3 py-1 border rounded disabled:opacity-50"
          >
            {t('review_queue.prev_page')}
          </button>
          <span className="text-sm text-gray-600">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            disabled={page >= totalPages}
            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
            className="text-sm px-3 py-1 border rounded disabled:opacity-50"
          >
            {t('review_queue.next_page')}
          </button>
        </div>
      )}

      {previewArtifact && (
        <Suspense fallback={null}>
          <FileViewerModal
            artifactId={previewArtifact.id}
            fileName={previewArtifact.name}
            onClose={() => setPreviewArtifact(null)}
          />
        </Suspense>
      )}
    </div>
  );
};
