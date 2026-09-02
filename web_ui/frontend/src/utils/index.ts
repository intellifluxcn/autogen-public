// Shared utility functions for styling, formatting, and validation.

import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { StageStatus, PipelineStage, MessageType, type Project } from '../types';

const PIPELINE_STAGE_ORDER: PipelineStage[] = [
  PipelineStage.FIND,
  PipelineStage.ANALYZE,
  PipelineStage.DOWNLOAD,
  PipelineStage.QUALIFY,
];

/** Stage driving the pipeline UI (cards may be authoritative vs. stale current_stage after reload/resume). */
export function getDisplayCurrentStage(project: Project): PipelineStage {
  if (project.stages) {
    for (const stage of PIPELINE_STAGE_ORDER) {
      const info = project.stages[stage];
      if (
        info &&
        (info.status === StageStatus.IN_PROGRESS || info.status === StageStatus.PAUSED)
      ) {
        return stage;
      }
    }
    if (project.status === StageStatus.ERROR || project.status === StageStatus.CANCELLED) {
      for (const stage of [...PIPELINE_STAGE_ORDER].reverse()) {
        const info = project.stages[stage];
        if (
          info &&
          (info.status === StageStatus.ERROR || info.status === StageStatus.CANCELLED)
        ) {
          return stage;
        }
      }
    }
  }
  if (project.status === StageStatus.COMPLETED || project.current_stage === PipelineStage.COMPLETE) {
    return PipelineStage.COMPLETE;
  }
  return project.current_stage || PipelineStage.FIND;
}
import { getLanguage, t } from '../i18n';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function getStageStatusColor(status: StageStatus): string {
  switch (status) {
    case StageStatus.PENDING:
      return 'text-gray-500 bg-gray-100';
    case StageStatus.IN_PROGRESS:
      return 'text-blue-700 bg-blue-100';
    case StageStatus.PAUSED:
      return 'text-yellow-700 bg-yellow-100';
    case StageStatus.COMPLETED:
      return 'text-green-700 bg-green-100';
    case StageStatus.ERROR:
      return 'text-red-700 bg-red-100';
    case StageStatus.CANCELLED:
      return 'text-gray-700 bg-gray-100';
    default:
      return 'text-gray-500 bg-gray-100';
  }
}

export function getStageColor(stage: PipelineStage): string {
  switch (stage) {
    case PipelineStage.FIND:
      return 'text-stage-find';
    case PipelineStage.ANALYZE:
      return 'text-stage-analyze';
    case PipelineStage.DOWNLOAD:
      return 'text-stage-download';
    case PipelineStage.QUALIFY:
      return 'text-teal-600';
    case PipelineStage.COMPLETE:
      return 'text-stage-complete';
    default:
      return 'text-gray-500';
  }
}

export function getProgressBarColor(stage: PipelineStage): string {
  switch (stage) {
    case PipelineStage.FIND:
      return 'bg-stage-find';
    case PipelineStage.ANALYZE:
      return 'bg-stage-analyze';
    case PipelineStage.DOWNLOAD:
      return 'bg-stage-download';
    case PipelineStage.QUALIFY:
      return 'bg-teal-500';
    case PipelineStage.COMPLETE:
      return 'bg-stage-complete';
    default:
      return 'bg-gray-400';
  }
}

export function getMessageTypeColor(messageType: MessageType): string {
  switch (messageType) {
    case MessageType.SYSTEM:
      return 'text-gray-700';
    case MessageType.USER:
      return 'text-blue-700';
    case MessageType.ERROR:
      return 'text-red-700';
    case MessageType.WARNING:
      return 'text-yellow-700';
    case MessageType.INFO:
      return 'text-blue-600';
    case MessageType.TEAM:
      return 'text-purple-700';
    default:
      return 'text-gray-700';
  }
}

/**
 * Parse API/DB timestamps as an instant. Naive ISO strings are UTC (backend uses UTC for TIMESTAMP).
 */
export function parseApiDate(timestamp: string): Date {
  const raw = timestamp.trim();
  if (!raw) {
    return new Date(NaN);
  }
  if (/[zZ]$/.test(raw)) {
    return new Date(raw);
  }
  if (/[+-]\d{2}:\d{2}$/.test(raw) || /[+-]\d{4}$/.test(raw)) {
    return new Date(raw);
  }
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(normalized)) {
    return new Date(`${normalized}Z`);
  }
  return new Date(raw);
}

/** Absolute date-time in the user's locale and time zone (YYYY-MM-DD HH:mm). */
export function formatLocaleDateTime(timestamp: string): string {
  const loc = getLanguage() === 'zh' ? 'zh-CN' : 'en-US';
  return parseApiDate(timestamp).toLocaleString(loc, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatTimestamp(timestamp: string): string {
  const date = parseApiDate(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return t('time.relative.just_now');
  if (diffMins < 60) return t('time.relative.minutes_ago', { n: diffMins });
  if (diffHours < 24) return t('time.relative.hours_ago', { n: diffHours });
  if (diffDays < 7) return t('time.relative.days_ago', { n: diffDays });

  const loc = getLanguage() === 'zh' ? 'zh-CN' : 'en-US';
  return date.toLocaleDateString(loc);
}

/**
 * Elapsed time for this pipeline stage (find / analyze / download):
 * - Uses the stage row's `start_time` (when the current attempt started in the DB).
 * - Non-terminal (pending / in progress / paused): end is always "now" — duration matches
 *   wall time since this stage began, including across pause/resume.
 * - Terminal (completed / error / cancelled): uses `end_time` when present.
 * Ignores stale `end_time` on non-terminal stages (avoids negative durations).
 */
export function formatDuration(
  startTime?: string,
  endTime?: string,
  stageStatus?: StageStatus,
): string {
  if (!startTime) return t('time.placeholder.dash');

  const start = parseApiDate(startTime);
  if (Number.isNaN(start.getTime())) return t('time.placeholder.dash');

  const terminal =
    stageStatus === StageStatus.COMPLETED ||
    stageStatus === StageStatus.ERROR ||
    stageStatus === StageStatus.CANCELLED;

  let end: Date;
  if (terminal && endTime) {
    end = parseApiDate(endTime);
    if (Number.isNaN(end.getTime())) {
      end = new Date();
    }
  } else {
    end = new Date();
  }

  let diffMs = end.getTime() - start.getTime();
  if (diffMs < 0) {
    diffMs = 0;
  }

  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHours = Math.floor(diffMins / 60);

  if (diffMs > 0 && diffSecs < 1) {
    return t('time.duration.less_than_second');
  }
  if (diffSecs < 60) return t('time.duration.seconds', { n: diffSecs });
  if (diffMins < 60)
    return t('time.duration.min_sec', { m: diffMins, s: diffSecs % 60 });
  return t('time.duration.hr_min', { h: diffHours, m: diffMins % 60 });
}

/** Format a precomputed total duration in seconds using localized units. */
export function formatDurationSeconds(totalSeconds: number): string {
  if (totalSeconds < 60) return t('time.duration.seconds', { n: totalSeconds });
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  if (mins < 60) {
    return t('time.duration.min_sec', { m: mins, s: secs });
  }
  const hours = Math.floor(mins / 60);
  return t('time.duration.hr_min', { h: hours, m: mins % 60 });
}

export function formatProgress(progress: number): string {
  return `${Math.round(progress * 100)}%`;
}

export function getStageName(stage: PipelineStage): string {
  switch (stage) {
    case PipelineStage.FIND:     return t('pipeline.find');
    case PipelineStage.ANALYZE:  return t('pipeline.analyze');
    case PipelineStage.DOWNLOAD: return t('pipeline.download');
    case PipelineStage.COMPLETE: return t('pipeline.complete');
    default:                     return '';
  }
}

export function getStageDescription(stage: PipelineStage): string {
  switch (stage) {
    case PipelineStage.FIND:
      return t('pipeline.find_desc');
    case PipelineStage.ANALYZE:
      return t('pipeline.analyze_desc');
    case PipelineStage.DOWNLOAD:
      return t('pipeline.download_desc');
    case PipelineStage.COMPLETE:
      return t('pipeline.complete_desc');
    default:
      return '';
  }
}

export function getStatusIcon(status: StageStatus): string {
  switch (status) {
    case StageStatus.PENDING:
      return '⏳';
    case StageStatus.IN_PROGRESS:
      return '🔄';
    case StageStatus.COMPLETED:
      return '✅';
    case StageStatus.ERROR:
      return '❌';
    case StageStatus.CANCELLED:
      return '⏹️';
    default:
      return '❓';
  }
}

export function isValidProjectName(name: string): boolean {
  return name.trim().length >= 3 && name.trim().length <= 100;
}

export function isValidQuery(query: string): boolean {
  // upper bound aligned with backend CreateProjectRequest.query
  // (max_length=2000) so long PubMed advanced queries (multi-field +
  // multi-boolean) aren't silently truncated at the frontend.
  return query.trim().length >= 10 && query.trim().length <= 2000;
}

export function sortProjectsByDate<T extends { created_at: string; updated_at?: string }>(projects: T[]): T[] {
  return [...projects].sort(
    (a, b) => {
      const bUpdated = parseApiDate(b.updated_at || b.created_at).getTime();
      const aUpdated = parseApiDate(a.updated_at || a.created_at).getTime();
      if (bUpdated !== aUpdated) {
        return bUpdated - aUpdated;
      }
      return parseApiDate(b.created_at).getTime() - parseApiDate(a.created_at).getTime();
    }
  );
}

export function saveToLocalStorage(key: string, value: any): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn('Failed to save to localStorage:', error);
  }
}

export function loadFromLocalStorage<T>(key: string, defaultValue: T): T {
  try {
    const item = localStorage.getItem(key);
    return item ? JSON.parse(item) : defaultValue;
  } catch (error) {
    console.warn('Failed to load from localStorage:', error);
    return defaultValue;
  }
}
