// Project detail page with overview, pipeline stages, artifacts, and messages tabs.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  AlertTriangle,
  Ban,
  Check,
  CheckCircle,
  Clock,
  Database,
  Dna,
  FileCheck,
  FileSearch,
  FileText,
  Hash,
  LockKeyhole,
  Mail,
  Pill,
  RefreshCw,
  ShieldAlert,
  Table2,
  Wrench,
  XCircle,
} from 'lucide-react';
import { databaseApi, ContactAuthorInfo } from '../services/databaseApi';
import { StageTimeline } from '../components/StageTimeline';
import { ArtifactList } from '../components/ArtifactList';
import { MessageLog } from '../components/MessageLog';
import { EmailComposeModal } from '../components/EmailComposeModal';
import { InputModal } from '../components/InputModal';
// Review tab uses an extracted view component instead of
// the deleted top-level ReviewQueuePage. Imported eagerly (not lazy) since
// it's just an inline tab body, not a separate route.
import { ReviewQueueView } from '../components/ReviewQueueView';
// pending-count side-channel call for the Overview CTA banner
// + Review tab badge — independent of the queue's visible status filter.
import { getReviewQueueCount } from '../services/reviewQueueApi';
import type { DbExecutionLog, FullProjectData, ArtifactTableRow, ProjectCost } from '../types/database';
import { cn, formatDurationSeconds, formatLocaleDateTime } from '../utils';
import { translateErrorMessage } from '../utils/error';
import { socketClient } from '../utils/socket';
import { useTranslation } from '../hooks/useTranslation';
import { useInputRequests } from '../hooks/useInputRequests';
import { useModels } from '../hooks/useModels';

// 'review' tab added as a peer to the other stage/observation tabs.
type TabType = 'overview' | 'stages' | 'artifacts' | 'messages' | 'download_logs' | 'review';
const PIPELINE_STAGE_NAMES = new Set(['find', 'analyze', 'download', 'qualify']);

export function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [data, setData] = useState<FullProjectData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [activeTab, setActiveTab] = useState<TabType>('overview');
  const [contactAuthors, setContactAuthors] = useState<ContactAuthorInfo[]>([]);
  const [emailModalData, setEmailModalData] = useState<ContactAuthorInfo | null>(null);
  const [sentEmails, setSentEmails] = useState<Set<number>>(new Set());
  const [executionLogs, setExecutionLogs] = useState<DbExecutionLog[]>([]);
  const [artifactRows, setArtifactRows] = useState<ArtifactTableRow[]>([]);
  const [selectedExecutionStage, setSelectedExecutionStage] = useState<'all' | 'find' | 'analyze' | 'download' | 'pipeline'>('all');
  const [selectedScreenshot, setSelectedScreenshot] = useState<string | null>(null);
  const [expandedScreenshotLogs, setExpandedScreenshotLogs] = useState<Set<number>>(new Set());
  // pending review count powers both the Overview CTA banner
  // and the Review tab badge. `null` = not-yet-resolved (so neither badge
  // nor banner renders until the side-channel call returns) — avoids the
  // visual "flash of 0" before the count resolves.
  const [pendingReviewCount, setPendingReviewCount] = useState<number | null>(null);
  const [projectCost, setProjectCost] = useState<ProjectCost | null>(null);
  const { currentInputRequest, submitInputResponse, closeInputModal } = useInputRequests({ projectId });

  // Pull env-resolved model defaults to render "Default (<model>)" hints
  // when the project's stored model fields are NULL ("use default" at
  // creation). Cached client-side for 24h via useModels so navigating
  // between projects doesn't re-hit /api/models each time.
  const usesDefault =
    !!data && (data.project.analysis_model == null || data.project.download_model == null);
  const { data: modelsData } = useModels({ enabled: usesDefault });
  const modelDefaults = modelsData?.defaults ?? {};

  const loadProjectData = useCallback(async () => {
    if (!projectId) return;

    setIsLoading(true);
    setError(null);

    // Fire all four endpoints in parallel — they're independent backend
    // routes (full / artifact-rows / execution-logs / contact-authors).
    // Only getProjectFull gates page render; the other three populate
    // secondary tabs/panels and stream in as they resolve so the user
    // doesn't stare at a single spinner for sum-of-all-roundtrips.
    const fullP = databaseApi.getProjectFull(projectId);
    const rowsP = databaseApi.getProjectArtifactRows(projectId).catch((e) => {
      console.error('Failed to load artifact rows:', e);
      return [] as ArtifactTableRow[];
    });
    // Was 2000 — dropped to 500 alongside the backend default change.
    // The Find Summary card only needs the most recent find_summary
    // row, and 500 still covers the Execution Logs tab for any
    // realistic project size. Each log row carries a JSONB payload so
    // halving the limit roughly halves the response size and CPU.
    const logsP = databaseApi.getProjectExecutionLogs(projectId, undefined, 500).catch((e) => {
      console.error('Failed to load execution logs:', e);
      return [] as DbExecutionLog[];
    });
    const authorsP = databaseApi.getContactAuthors(projectId).catch((e) => {
      console.error('Failed to load contact authors:', e);
      return null as ContactAuthorInfo[] | null;
    });
    // best-effort initial pending count for Overview CTA + tab
    // badge. ReviewQueueView's own onCountChange will refresh this when the
    // user enters the Review tab; until then this is the only source.
    const reviewCountP = getReviewQueueCount(projectId).catch((e) => {
      console.error('Failed to load review queue count:', e);
      return null as number | null;
    });
    const costP = databaseApi.getProjectCost(projectId).catch((e) => {
      console.error('Failed to load project cost:', e);
      return null as ProjectCost | null;
    });
    costP.then((c) => {
      if (c) setProjectCost(c);
    });

    // Apply secondary results as soon as each arrives — don't block the
    // primary spinner. Overview/Artifacts/Logs already fall back gracefully
    // when their slice of state is still empty.
    rowsP.then(setArtifactRows);
    logsP.then(setExecutionLogs);
    authorsP.then((authors) => {
      if (!authors) return;
      setContactAuthors(authors);
      const alreadySent = new Set(
        authors.filter((a) => a.email_sent_at).map((a) => a.artifact_id)
      );
      setSentEmails(alreadySent);
    });
    reviewCountP.then((n) => {
      if (n !== null) setPendingReviewCount(n);
    });

    try {
      const projectData = await fullP;
      setData(projectData);
    } catch (err) {
      setError(err as Error);
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const handleEmailSent = (artifactId: number) => {
    setSentEmails(prev => new Set(prev).add(artifactId));
  };

  const toggleScreenshotLog = useCallback((logId: number) => {
    setExpandedScreenshotLogs((prev) => {
      const next = new Set(prev);
      if (next.has(logId)) {
        next.delete(logId);
      } else {
        next.add(logId);
      }
      return next;
    });
  }, []);

  const groupedExecutionLogs = useMemo(() => {
    const byStage = new Map<string, DbExecutionLog[]>();
    for (const log of executionLogs) {
      const stage = String(log.stage_name || 'pipeline');
      if (!byStage.has(stage)) {
        byStage.set(stage, []);
      }
      byStage.get(stage)!.push(log);
    }

    for (const logs of byStage.values()) {
      logs.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }

    const downloadLogs = byStage.get('download') || [];
    const groups = new Map<string, DbExecutionLog[]>();
    for (const log of downloadLogs) {
      const key = String((log.payload || {}).paper_name || 'general');
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key)!.push(log);
    }
    for (const logs of groups.values()) {
      logs.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }
    const downloadByPaper = Array.from(groups.entries()).sort((a, b) => a[0].localeCompare(b[0]));
    return {
      find: byStage.get('find') || [],
      analyze: byStage.get('analyze') || [],
      pipeline: byStage.get('pipeline') || [],
      downloadByPaper,
    };
  }, [executionLogs]);

  const hasVisibleExecutionLogs = useMemo(() => {
    if (selectedExecutionStage === 'all') {
      return (
        groupedExecutionLogs.find.length > 0
        || groupedExecutionLogs.analyze.length > 0
        || groupedExecutionLogs.downloadByPaper.length > 0
        || groupedExecutionLogs.pipeline.length > 0
      );
    }
    if (selectedExecutionStage === 'download') {
      return groupedExecutionLogs.downloadByPaper.length > 0;
    }
    return groupedExecutionLogs[selectedExecutionStage].length > 0;
  }, [groupedExecutionLogs, selectedExecutionStage]);

  useEffect(() => {
    loadProjectData();
  }, [loadProjectData]);

  // Debounce burst-y WebSocket-driven reloads. Without this, every
  // pipeline message_added (hundreds during find) would fire 5 parallel
  // REST calls (full / artifact-rows / execution-logs limit=2000 /
  // contact-authors / review-queue-count). The 1s window collapses a
  // burst into a single reload while still feeling real-time.
  const reloadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Synchronous dedup for WS message_added events. Multiple listeners
  // may be registered (App.tsx's useProjects + this page, plus any
  // useEffect re-run leak), and React batches setData callbacks so the
  // in-state dedup `tail.some(...)` always sees the same `prev` for
  // all concurrent calls — missing every duplicate. A ref-backed Map
  // updates synchronously so the SECOND handler in the same tick
  // already sees the key from the FIRST.
  const recentMsgKeysRef = useRef<Map<string, number>>(new Map());
  const RELOAD_DEBOUNCE_MS = 1000;

  useEffect(() => {
    if (!projectId) return;

    const socket = socketClient.connect();
    const joinCurrentProject = () => socketClient.joinProject(projectId);
    const shouldRefreshForProject = (eventData: any) =>
      String(eventData?.project_id || '') === projectId;

    const scheduleReload = () => {
      if (reloadTimerRef.current) clearTimeout(reloadTimerRef.current);
      reloadTimerRef.current = setTimeout(() => {
        reloadTimerRef.current = null;
        loadProjectData();
      }, RELOAD_DEBOUNCE_MS);
    };

    const refreshIfMatch = (eventData: any) => {
      if (shouldRefreshForProject(eventData)) scheduleReload();
    };

    // message_added fires extremely frequently during pipeline runs. Skip
    // the full reload and patch the local messages array in place using
    // the event payload (which already carries the full Message dict).
    //
    // Dedup via recentMsgKeysRef: ref updates are synchronous, so N
    // listeners (App.tsx useProjects + ProjectDetail + any leaked
    // re-subscription) firing for the same event will see the key set
    // by the FIRST call and short-circuit. In-state dedup wasn't
    // sufficient because setData(prev => ...) callbacks all receive
    // the same pre-batch `prev`, so tail.some() always sees no dup.
    const handleMessageAdded = (eventData: any) => {
      if (!shouldRefreshForProject(eventData)) return;
      const msg = eventData?.data?.message;
      if (!msg) return;
      const stage = eventData?.data?.stage ?? '';
      const teamName = msg.team_name ?? '';
      const content = msg.content ?? '';
      const timestamp = msg.timestamp ?? new Date().toISOString();

      const dedupKey = `${content}|${timestamp}|${teamName}`;
      const now = Date.now();
      const seenAt = recentMsgKeysRef.current.get(dedupKey);
      if (seenAt !== undefined && now - seenAt < 5000) {
        return;  // duplicate within 5s window — drop
      }
      recentMsgKeysRef.current.set(dedupKey, now);
      // Garbage-collect entries older than 10s so the Map stays bounded
      // on long-running pipelines (5000+ messages over hours).
      if (recentMsgKeysRef.current.size > 200) {
        for (const [k, t] of recentMsgKeysRef.current) {
          if (now - t > 10000) recentMsgKeysRef.current.delete(k);
        }
      }

      const patched = {
        // DbMessage.id is numeric in the DB; synthesise a negative
        // sentinel so React keys stay stable until the next debounced
        // reload replaces the row with the real one.
        id: -Date.now(),
        stage_name: stage,
        team_name: teamName,
        content: content,
        message_type: msg.message_type ?? 'info',
        timestamp: timestamp,
      } as unknown as FullProjectData['messages'][number];
      setData((prev) => (prev ? { ...prev, messages: [...prev.messages, patched] } : prev));
    };

    // stage_updated: patch the affected stage's status locally so the
    // Stages tab badge flips immediately, then schedule the debounced
    // reload to pick up new artifacts/logs that usually appear at stage
    // transitions (status going to completed/failed adds rows we have
    // no other way to learn about — see ProjectDetail design note).
    const handleStageUpdated = (eventData: any) => {
      if (!shouldRefreshForProject(eventData)) return;
      const stageName = eventData?.data?.stage as string | undefined;
      const stageStatus = eventData?.data?.status as string | undefined;
      if (stageName && stageStatus) {
        setData((prev) => prev ? {
          ...prev,
          stages: prev.stages.map((s) =>
            s.stage_name === stageName
              ? ({ ...s, status: stageStatus } as typeof s)
              : s
          ),
        } : prev);
      }
      scheduleReload();
    };

    joinCurrentProject();
    socket.on('connect', joinCurrentProject);

    // Intentionally NOT subscribed:
    //   * progress_updated — fires per paper/dataset (high frequency); the
    //     UI here has no per-event progress display, and the message_added
    //     stream already shows live progress text.
    //   * status_updated — orchestrator UI status banner ("Found 100
    //     papers" etc.), not a project.status update.
    // Dropping these is the bulk of the reload-storm reduction.
    const unsubscribers = [
      socketClient.on('project_updated', refreshIfMatch),
      socketClient.on('stage_updated', handleStageUpdated),
      socketClient.on('message_added', handleMessageAdded),
      socketClient.on('project_deleted', refreshIfMatch),
    ];

    return () => {
      if (reloadTimerRef.current) {
        clearTimeout(reloadTimerRef.current);
        reloadTimerRef.current = null;
      }
      socket.off('connect', joinCurrentProject);
      unsubscribers.forEach((unsubscribe) => unsubscribe());
    };
  }, [loadProjectData, projectId]);

  const getTotalDuration = () => {
    if (!data) return 0;
    return data.stages.reduce((total, stage) => total + (stage.duration_seconds || 0), 0);
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="w-6 h-6 text-green-600" />;
      case 'running':
        return <Clock className="w-6 h-6 text-yellow-600" />;
      case 'failed':
        return <XCircle className="w-6 h-6 text-red-600" />;
      default:
        return null;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'text-green-800 bg-green-100';
      case 'running':
        return 'text-yellow-800 bg-yellow-100';
      case 'failed':
        return 'text-red-800 bg-red-100';
      default:
        return 'text-gray-800 bg-gray-100';
    }
  };

  const getStatusLabel = (status: string) => {
    const key = `status.${String(status || '').toLowerCase()}`;
    const translated = t(key);
    return translated === key ? status : translated;
  };

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 border-3 border-blue-600 border-t-transparent rounded-full animate-spin" />
          <span className="text-gray-700 text-lg">{t('detail.loading')}</span>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md">
          <div className="flex items-center space-x-2 mb-3">
            <AlertTriangle className="w-6 h-6 text-red-500" />
            <h3 className="text-lg font-semibold text-red-800">{t('detail.failed_load')}</h3>
          </div>
          <p className="text-sm text-red-700 mb-4">
            {translateErrorMessage(error?.message, t, 'detail.not_found')}
          </p>
          <div className="flex space-x-3">
            <button
              onClick={() => navigate('/')}
              className="flex items-center space-x-2 px-4 py-2 bg-gray-200 text-gray-800 rounded-lg hover:bg-gray-300 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              <span>{t('detail.back_home')}</span>
            </button>
            <button
              onClick={loadProjectData}
              className="flex items-center space-x-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              <span>{t('detail.try_again')}</span>
            </button>
          </div>
        </div>
      </div>
    );
  }

  const visibleStages = data.stages.filter(stage => PIPELINE_STAGE_NAMES.has(stage.stage_name));

  const tabs: { id: TabType; label: string; count?: number }[] = [
    { id: 'overview', label: t('detail.overview') },
    { id: 'stages', label: t('detail.stages'), count: visibleStages.length },
    { id: 'artifacts', label: t('detail.artifacts'), count: data.artifacts.length },
    { id: 'messages', label: t('detail.messages'), count: data.messages.length },
    { id: 'download_logs', label: t('detail.download_logs'), count: executionLogs.length },
    // Review tab is last because it's the "next step" after
    // find/analyze/download have produced Tier 2/3 artifacts.
    // count is bound to pendingReviewCount; null (not yet
    // resolved) → undefined → no badge renders, avoiding a "0 → real" flash.
    { id: 'review', label: t('detail.review'), count: pendingReviewCount ?? undefined },
  ];

  return (
    <div className="min-h-screen bg-gray-200">
      <div className="bg-white border-b border-gray-200 px-4 py-3 sm:px-6 sm:py-4">
        <div className="max-w-7xl mx-auto">
          <button
            onClick={() => navigate('/')}
            className="flex items-center space-x-2 text-gray-600 hover:text-gray-900 mb-3 sm:mb-4 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            <span>{t('detail.back_home')}</span>
          </button>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex-1 min-w-0">
              <h1 className="text-xl sm:text-3xl font-bold text-gray-900 mb-2 break-all">{data.project.name}</h1>
              <p className="text-gray-600 mb-3 break-words text-sm sm:text-base">{data.project.research_query}</p>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:text-sm text-gray-600">
                <span>{t('detail.created')}: {formatLocaleDateTime(data.project.created_at)}</span>
                <span className="hidden sm:inline">•</span>
                <span>{t('detail.updated')}: {formatLocaleDateTime(data.project.updated_at)}</span>
                <span className="hidden sm:inline">•</span>
                <span>{t('detail.duration')}: {formatDurationSeconds(getTotalDuration())}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 sm:gap-3 self-start sm:ml-4 shrink-0">
              {getStatusIcon(data.project.status)}
              <span className={cn(
                "px-3 py-1.5 sm:px-4 sm:py-2 rounded-lg font-semibold capitalize text-sm sm:text-base",
                getStatusColor(data.project.status)
              )}>
                {getStatusLabel(data.project.status)}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="bg-white border-b border-gray-200">
        <div className="max-w-7xl mx-auto px-4 sm:px-6">
          <div className="flex space-x-6 sm:space-x-8 overflow-x-auto">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  "py-4 px-2 border-b-2 font-medium text-sm transition-colors whitespace-nowrap shrink-0",
                  activeTab === tab.id
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-gray-600 hover:text-gray-900 hover:border-gray-300"
                )}
              >
                {tab.label}
                {tab.count !== undefined && (
                  <span className={cn(
                    "ml-2 px-2 py-0.5 rounded-full text-xs",
                    activeTab === tab.id
                      ? "bg-blue-100 text-blue-600"
                      : "bg-gray-100 text-gray-600"
                  )}>
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
        {activeTab === 'overview' && (() => {
          const analysisArtifacts = data.artifacts.filter(a => a.artifact_type === 'analysis');
          const downloadedArtifacts = data.artifacts.filter(a => a.artifact_type === 'dataset');
          const embeddedArtifacts = data.artifacts.filter(a => a.artifact_type === 'embedded_dataset');
          const evidenceArtifacts = data.artifacts.filter(a => a.artifact_type === 'acquisition_evidence');

          // Find Stage Summary — pulled from the most recent
          // event_type='find_summary' execution_log row (written by
          // find/team.py at the end of every find run). Falls back to null
          // for projects that ran before this feature shipped.
          const findSummaryLog = executionLogs
            .filter(l => l.event_type === 'find_summary')
            .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
          const findSummary = findSummaryLog
            ? (findSummaryLog.payload as Record<string, number> | undefined)
            : undefined;
          const awaitingExternalCount = data.artifacts.filter(a => a.acquisition_status === 'awaiting_external').length;
          const classifyDataSummaryRow = (row: ArtifactTableRow): string => {
            if (row.datasetCount > 0) return 'repository_dataset';
            if (row.embeddedDatasetCount > 0) return 'embedded_dataset';

            const statuses = row.acquisitionEvidence
              .map(e => e.acquisitionStatus)
              .filter(Boolean) as string[];
            const sources = row.acquisitionEvidence
              .map(e => e.acquisitionSource)
              .filter(Boolean) as string[];

            if (statuses.some(s => ['blocked_by_auth', 'blocked_by_approval'].includes(s))) {
              return 'controlled_access';
            }
            if (statuses.includes('browser_verification_blocked')) return 'browser_blocked';
            if (statuses.includes('accession_not_identified')) return 'accession_missing';
            if (statuses.includes('pdf_only_no_structured_dataset')) return 'pdf_only';
            if (statuses.includes('no_valid_dataset_files')) return 'no_valid_dataset';
            if (
              statuses.some(s => ['author_contact_required', 'awaiting_external'].includes(s))
              || sources.includes('contact_author')
            ) {
              return 'contact_author';
            }
            if (row.evidenceCount > 0) return 'acquisition_evidence';

            return row.dataClassificationFlag && row.dataClassificationFlag !== 'no_analysis'
              ? row.dataClassificationFlag
              : 'no_analysis';
          };

          const summaryRows = artifactRows.length > 0 ? artifactRows : [];
          const flagCounts = summaryRows.length > 0
            ? summaryRows.reduce((acc, row) => {
                const flag = classifyDataSummaryRow(row);
                acc[flag] = (acc[flag] || 0) + 1;
                return acc;
              }, {} as Record<string, number>)
            : analysisArtifacts.reduce((acc, a) => {
                const flag = a.data_classification_flag || 'no_analysis';
                acc[flag] = (acc[flag] || 0) + 1;
                return acc;
              }, {} as Record<string, number>);

          const contactAuthorPapers = analysisArtifacts
            .filter(a => a.data_classification_flag === 'contact_author')
            .map(a => {
              const name = (a.file_name || a.file_path.split('/').pop() || '')
                .replace(/\.md$/i, '')
                .replace(/_/g, ' ');
              return name;
            });

          const flagConfig: Record<string, { label: string; bgColor: string; textColor: string }> = {
            repository_dataset: { label: t('data.repository_dataset'), bgColor: 'bg-blue-100', textColor: 'text-blue-700' },
            embedded_dataset:   { label: t('data.embedded_dataset'), bgColor: 'bg-amber-100', textColor: 'text-amber-700' },
            acquisition_evidence: { label: t('data.acquisition_evidence'), bgColor: 'bg-slate-100', textColor: 'text-slate-700' },
            controlled_access:  { label: t('data.controlled_access'), bgColor: 'bg-red-100', textColor: 'text-red-700' },
            browser_blocked:    { label: t('data.browser_blocked'), bgColor: 'bg-red-100', textColor: 'text-red-700' },
            accession_missing:  { label: t('data.accession_missing'), bgColor: 'bg-orange-100', textColor: 'text-orange-700' },
            pdf_only:           { label: t('data.pdf_only'), bgColor: 'bg-gray-100', textColor: 'text-gray-700' },
            no_valid_dataset:   { label: t('data.no_valid_dataset'), bgColor: 'bg-gray-100', textColor: 'text-gray-700' },
            both_data:          { label: t('data.both'), bgColor: 'bg-green-100', textColor: 'text-green-700' },
            sequencing_only:    { label: t('data.sequencing_only'), bgColor: 'bg-yellow-100', textColor: 'text-yellow-700' },
            drug_only:          { label: t('data.drug_only'), bgColor: 'bg-yellow-100', textColor: 'text-yellow-700' },
            manual_required:    { label: t('data.manual'), bgColor: 'bg-orange-100', textColor: 'text-orange-700' },
            contact_author:     { label: t('data.contact'), bgColor: 'bg-gray-100', textColor: 'text-gray-600' },
          };
          const flagIconMap = {
            repository_dataset: Database,
            embedded_dataset: Table2,
            acquisition_evidence: FileSearch,
            controlled_access: LockKeyhole,
            browser_blocked: ShieldAlert,
            accession_missing: Hash,
            pdf_only: FileText,
            no_valid_dataset: Ban,
            both_data: FileCheck,
            sequencing_only: Dna,
            drug_only: Pill,
            manual_required: Wrench,
            contact_author: Mail,
          };

          return (
            <div className="space-y-6">
              {/* pending-review CTA banner — only renders when
                  the side-channel count has resolved (≠ null) AND there's
                  something to review (> 0). Clickable row jumps to Review tab. */}
              {pendingReviewCount !== null && pendingReviewCount > 0 && (
                <button
                  type="button"
                  onClick={() => setActiveTab('review')}
                  className="w-full flex items-center justify-between gap-3 px-4 py-3 bg-amber-50 border border-amber-200 text-amber-800 rounded-lg hover:bg-amber-100 transition-colors text-left"
                >
                  <div className="flex items-center gap-2">
                    <Mail className="w-4 h-4 shrink-0" />
                    <span className="text-sm font-medium">
                      {t('detail.review_cta_pending', { count: pendingReviewCount })}
                    </span>
                  </div>
                  <span className="text-amber-700 text-base">→</span>
                </button>
              )}
              <div className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
                <div className="space-y-6">
                  <div>
                    <h2 className="text-xl font-semibold text-gray-900 mb-4">{t('detail.project_overview')}</h2>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                      <div>
                        <span className="text-gray-600">{t('detail.project_id')}:</span>
                        <span className="ml-2 font-mono text-gray-900">{data.project.id}</span>
                      </div>
                      <div>
                        <span className="text-gray-600">{t('detail.status')}:</span>
                        <span className="ml-2 font-semibold capitalize">{getStatusLabel(data.project.status)}</span>
                      </div>
                      <div>
                        <span className="text-gray-600">{t('detail.stages_completed')}:</span>
                        <span className="ml-2 font-semibold">
                          {visibleStages.filter(s => s.status === 'completed').length} / {visibleStages.length}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-600">{t('detail.total_artifacts')}:</span>
                        <span className="ml-2 font-semibold">{data.artifacts.length}</span>
                      </div>
                      <div>
                        <span className="text-gray-600">{t('detail.analysis_model')}:</span>
                        <span
                          className="ml-2 font-semibold"
                          title={data.project.analysis_model ? undefined : t('detail.model_default_hint')}
                        >
                          {data.project.analysis_model
                            ?? (modelDefaults.analysis
                              ? t('detail.model_default_with_value', { model: modelDefaults.analysis })
                              : t('detail.model_default'))}
                        </span>
                      </div>
                      <div>
                        <span className="text-gray-600">{t('detail.download_model')}:</span>
                        <span
                          className="ml-2 font-semibold"
                          title={data.project.download_model ? undefined : t('detail.model_default_hint')}
                        >
                          {data.project.download_model
                            ?? (modelDefaults.download
                              ? t('detail.model_default_with_value', { model: modelDefaults.download })
                              : t('detail.model_default'))}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div>
                    <h3 className="text-lg font-semibold text-gray-900 mb-4">{t('detail.quick_stats')}</h3>
                    <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                      <div className="bg-blue-100 rounded-lg p-4">
                        <div className="text-2xl font-bold text-blue-700">
                          {data.artifacts.filter(a => a.artifact_type === 'paper').length}
                        </div>
                        <div className="text-sm text-blue-900">{t('detail.papers')}</div>
                      </div>
                      <div className="bg-blue-100 rounded-lg p-4">
                        <div className="text-2xl font-bold text-blue-700">
                          {data.analyze_processed_count ??
                            data.artifacts.filter(a => a.artifact_type === 'analysis').length}
                        </div>
                        <div className="text-sm text-blue-900">{t('detail.analyses')}</div>
                      </div>
                      <div className="bg-blue-100 rounded-lg p-4">
                        <div className="text-2xl font-bold text-blue-700">
                          {downloadedArtifacts.length}
                        </div>
                        <div className="text-sm text-blue-900">{t('detail.datasets')}</div>
                      </div>
                      <div className="bg-amber-100 rounded-lg p-4">
                        <div className="text-2xl font-bold text-amber-700">
                          {embeddedArtifacts.length}
                        </div>
                        <div className="text-sm text-amber-900">{t('detail.embedded_datasets')}</div>
                      </div>
                      <div className="bg-slate-100 rounded-lg p-4">
                        <div className="text-2xl font-bold text-slate-700">
                          {evidenceArtifacts.length}
                        </div>
                        <div className="text-sm text-slate-900">{t('detail.acquisition_evidence')}</div>
                      </div>
                    </div>
                    {awaitingExternalCount > 0 && (
                      <div className="mt-4 flex flex-wrap gap-2">
                        <span className="inline-flex items-center rounded-full border border-gray-200 bg-gray-50 px-3 py-1 text-xs font-medium text-gray-700">
                          {t('detail.awaiting_reply')}: {awaitingExternalCount}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {findSummary && (
                <div className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">
                    {t('detail.find_summary_title')}
                  </h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="bg-gray-50 rounded-lg p-4">
                      <div className="text-2xl font-bold text-gray-800">
                        {findSummary.total_candidates ?? 0}
                      </div>
                      <div className="text-sm text-gray-600">
                        {t('detail.find_summary_total_candidates')}
                      </div>
                    </div>
                    <div className="bg-amber-50 rounded-lg p-4">
                      <div className="text-2xl font-bold text-amber-700">
                        {findSummary.cross_project_skipped ?? 0}
                      </div>
                      <div className="text-sm text-amber-900">
                        {t('detail.find_summary_cross_skipped')}
                      </div>
                    </div>
                    <div className="bg-green-50 rounded-lg p-4">
                      <div className="text-2xl font-bold text-green-700">
                        {findSummary.total_obtained ?? 0}
                        <span className="text-base font-normal text-green-700/70">
                          {' / '}{findSummary.requested_max_papers ?? '-'}
                        </span>
                      </div>
                      <div className="text-sm text-green-900">
                        {t('detail.find_summary_downloaded')}
                      </div>
                    </div>
                    <div className={cn(
                      'rounded-lg p-4',
                      (findSummary.shortfall ?? 0) > 0
                        ? 'bg-red-50' : 'bg-gray-50'
                    )}>
                      <div className={cn(
                        'text-2xl font-bold',
                        (findSummary.shortfall ?? 0) > 0
                          ? 'text-red-700' : 'text-gray-700'
                      )}>
                        {findSummary.shortfall ?? 0}
                      </div>
                      <div className={cn(
                        'text-sm',
                        (findSummary.shortfall ?? 0) > 0
                          ? 'text-red-900' : 'text-gray-700'
                      )}>
                        {t('detail.find_summary_shortfall')}
                      </div>
                    </div>
                  </div>
                  {(findSummary.already_downloaded_resume ?? 0) > 0 && (
                    <div className="mt-3 text-xs text-gray-500">
                      {t('detail.find_summary_resumed')}: {findSummary.already_downloaded_resume}
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
                  <h3 className="text-lg font-semibold text-gray-900 mb-4">{t('detail.data_types_summary')}</h3>
                  <div className="space-y-3">
                    {Object.entries(flagCounts)
                      .filter(([flag, count]) => count > 0 && flag !== 'no_analysis')
                      .map(([flag, count]) => {
                        const config = flagConfig[flag] || flagConfig.contact_author;
                        const Icon = flagIconMap[flag as keyof typeof flagIconMap] || Mail;
                        return (
                          <div key={flag} className="flex items-center justify-between">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className={cn(
                                "inline-flex shrink-0 items-center gap-1 px-2 py-1 rounded text-xs font-medium",
                                config.bgColor,
                                config.textColor
                              )}>
                                <Icon className="w-3 h-3 shrink-0" />
                                {config.label}
                              </span>
                              <span className="truncate text-xs text-gray-500">
                                {t(`data.explain.${flag}`)}
                              </span>
                            </div>
                            <span className="text-2xl font-bold text-gray-700">{count}</span>
                          </div>
                        );
                      })}
                    {Object.keys(flagCounts).filter(k => k !== 'no_analysis').length === 0 && (
                      <p className="text-sm text-gray-500 italic">{t('detail.no_data_types')}</p>
                    )}
                  </div>
                </div>

                <div className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
                  <div className="flex items-center space-x-2 mb-4">
                    <Mail className="w-5 h-5 text-gray-700" />
                    <h3 className="text-lg font-semibold text-gray-900">{t('detail.contact_author')}</h3>
                  </div>
                  <div className="space-y-4">
                    {contactAuthors.length > 0 ? (
                      contactAuthors.map((paper, idx) => (
                        <div key={idx} className="border-b border-gray-100 last:border-b-0 pb-3 last:pb-0">
                          <div className="flex items-start justify-between">
                            <div className="flex-1">
                              <div className="text-base font-bold text-gray-900 mb-1">
                                {paper.paper_name}
                              </div>
                              {paper.authors.length > 0 && (
                                <div className="space-y-0.5 ml-4">
                                  {paper.authors.slice(0, 2).map((author, authorIdx) => (
                                    <div key={authorIdx} className="text-sm text-gray-600">
                                      {author}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                            <div className="ml-4 flex-shrink-0 flex items-center space-x-2">
                              {sentEmails.has(paper.artifact_id) ? (
                                <>
                                  <div className="flex items-center space-x-1 text-green-600">
                                    <Check className="w-5 h-5" />
                                    <span className="text-xs font-medium">{t('detail.sent')}</span>
                                  </div>
                                  <button
                                    onClick={() => setEmailModalData(paper)}
                                    className="rounded-full px-2.5 py-1 text-xs border border-blue-700 text-blue-700 hover:bg-blue-50 transition-colors"
                                  >
                                    {t('detail.email_again')}
                                  </button>
                                </>
                              ) : (
                                <button
                                  onClick={() => setEmailModalData(paper)}
                                  className="rounded-full px-3 py-1 text-xs bg-blue-800 text-white hover:bg-blue-900 transition-colors"
                                >
                                  {t('detail.email')}
                                </button>
                              )}
                            </div>
                          </div>
                        </div>
                      ))
                    ) : contactAuthorPapers.length > 0 ? (
                      contactAuthorPapers.map((paper, idx) => (
                        <div key={idx} className="flex items-start space-x-2 text-sm">
                          <span className="text-gray-400 mt-0.5">•</span>
                          <span className="text-gray-700">{paper}</span>
                        </div>
                      ))
                    ) : (
                      <p className="text-sm text-gray-500 italic">{t('detail.no_papers_require_contact')}</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })()}

        {activeTab !== 'overview' && (
          <div className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
            {activeTab === 'stages' && (
              <div>
                <h2 className="text-xl font-semibold text-gray-900 mb-6">{t('detail.pipeline_stages')}</h2>
                <StageTimeline stages={data.stages} />
              </div>
            )}

            {activeTab === 'artifacts' && (
              <div>
                <h2 className="text-xl font-semibold text-gray-900 mb-6">{t('detail.project_artifacts')}</h2>
                <ArtifactList artifacts={data.artifacts} artifactRows={artifactRows} projectCost={projectCost} />
              </div>
            )}

            {activeTab === 'messages' && (
              <div>
                <h2 className="text-xl font-semibold text-gray-900 mb-6">{t('detail.messages')}</h2>
                <MessageLog messages={data.messages} />
              </div>
            )}

            {activeTab === 'download_logs' && (
              <div>
                <h2 className="text-xl font-semibold text-gray-900 mb-6">
                  {t('detail.download_detailed_logs')}
                </h2>
                {executionLogs.length > 0 && (
                  <div className="space-y-3 mb-6">
                    <div className="flex items-center space-x-2">
                      <div className="flex space-x-2">
                        {(['all', 'find', 'analyze', 'download', 'pipeline'] as const).map((stage) => {
                          const label = stage === 'all'
                            ? t('messages.stage.all')
                            : stage === 'pipeline'
                              ? t('detail.pipeline')
                              : t(`stage.${stage}`);
                          return (
                            <button
                              key={stage}
                              onClick={() => setSelectedExecutionStage(stage)}
                              className={cn(
                                "px-3 py-1 rounded-full text-sm font-medium transition-colors",
                                selectedExecutionStage === stage
                                  ? "bg-blue-600 text-white"
                                  : "bg-gray-100 text-gray-700 hover:bg-gray-200"
                              )}
                            >
                              {label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                )}
                {executionLogs.length === 0 ? (
                  <div className="text-sm text-gray-500">
                    {t('detail.no_download_logs')}
                  </div>
                ) : (
                  <div className="space-y-8">
                    {(['find', 'analyze', 'pipeline'] as const).map((stage) => {
                      if (selectedExecutionStage !== 'all' && selectedExecutionStage !== stage) {
                        return null;
                      }
                      const logs = groupedExecutionLogs[stage];
                      if (logs.length === 0) {
                        return null;
                      }
                      const stageLabel = stage === 'pipeline' ? t('detail.pipeline') : t(`stage.${stage}`);
                      return (
                        <div key={stage} className="border border-gray-200 rounded-lg">
                          <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
                            <h3 className="font-semibold text-gray-900">{stageLabel}</h3>
                          </div>
                          <div className="divide-y divide-gray-100">
                            {logs.map((log) => (
                              <div key={log.id} className="p-4 space-y-2">
                                <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                                  <div className="flex items-center space-x-2 min-w-0">
                                    <span className={cn(
                                      'shrink-0 px-2 py-0.5 rounded text-xs font-medium',
                                      log.severity === 'error'
                                        ? 'bg-red-100 text-red-700'
                                        : log.severity === 'warning'
                                          ? 'bg-yellow-100 text-yellow-700'
                                          : 'bg-blue-100 text-blue-700'
                                    )}>
                                      {log.severity}
                                    </span>
                                    <span className="text-sm font-medium text-gray-800 break-all">{log.event_type}</span>
                                  </div>
                                  <span className="text-xs text-gray-500 whitespace-nowrap shrink-0">
                                    {formatLocaleDateTime(log.created_at)}
                                  </span>
                                </div>
                                <p className="text-sm text-gray-700 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{log.message}</p>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}

                    {(selectedExecutionStage === 'all' || selectedExecutionStage === 'download') &&
                      groupedExecutionLogs.downloadByPaper.map(([paperName, logs]) => (
                      <div key={paperName} className="border border-gray-200 rounded-lg">
                        <div className="px-4 py-3 border-b border-gray-200 bg-gray-50">
                          <h3 className="font-semibold text-gray-900">
                            {paperName === 'general' ? t('detail.general_download_logs') : paperName}
                          </h3>
                        </div>
                        <div className="divide-y divide-gray-100">
                          {logs.map((log) => (
                            <div key={log.id} className="p-4 space-y-3">
                              {(() => {
                                const screenshotPaths = Array.isArray((log.payload as { screenshot_paths?: unknown }).screenshot_paths)
                                  ? ((log.payload as { screenshot_paths: string[] }).screenshot_paths)
                                  : [];
                                const screenshotsExpanded = expandedScreenshotLogs.has(log.id);
                                const canExpandScreenshots = screenshotPaths.length > 3;
                                return (
                                  <>
                              <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
                                <div className="flex items-center space-x-2 min-w-0">
                                  <span className={cn(
                                    'shrink-0 px-2 py-0.5 rounded text-xs font-medium',
                                    log.severity === 'error'
                                      ? 'bg-red-100 text-red-700'
                                      : log.severity === 'warning'
                                        ? 'bg-yellow-100 text-yellow-700'
                                        : 'bg-blue-100 text-blue-700'
                                  )}>
                                    {log.severity}
                                  </span>
                                  <span className="text-sm font-medium text-gray-800 break-all">{log.event_type}</span>
                                </div>
                                <span className="text-xs text-gray-500 whitespace-nowrap shrink-0">
                                  {formatLocaleDateTime(log.created_at)}
                                </span>
                              </div>
                              <p className="text-sm text-gray-700 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{log.message}</p>
                              {screenshotPaths.length > 0 && (
                                <div className="space-y-2">
                                  <div className={cn('flex flex-wrap gap-2 overflow-hidden', !screenshotsExpanded && 'max-h-20')}>
                                    {screenshotPaths.map((imagePath) => {
                                      const imageUrl = databaseApi.getDownloadDebugImageUrl(projectId!, imagePath);
                                      return (
                                        <button
                                          key={imagePath}
                                          onClick={() => setSelectedScreenshot(imageUrl)}
                                          className="border border-gray-200 rounded-md overflow-hidden hover:opacity-90"
                                          title={imagePath}
                                        >
                                          <img
                                            src={imageUrl}
                                            alt={t('detail.download_debug_screenshot')}
                                            className="w-28 h-20 object-cover bg-gray-100"
                                          />
                                        </button>
                                      );
                                    })}
                                  </div>
                                  {canExpandScreenshots && (
                                    <button
                                      type="button"
                                      onClick={() => toggleScreenshotLog(log.id)}
                                      className="text-xs font-medium text-blue-600 hover:text-blue-700"
                                    >
                                      {screenshotsExpanded
                                        ? t('detail.hide_screenshots')
                                        : t('detail.show_all_screenshots', { count: screenshotPaths.length })}
                                    </button>
                                  )}
                                </div>
                              )}
                                  </>
                                );
                              })()}
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}

                    {!hasVisibleExecutionLogs && (
                      <div className="text-sm text-gray-500">
                        {t('detail.no_download_logs')}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Review tab — embeds the extracted ReviewQueueView,
                scoped to the current project via projectId prop.
                wires onCountChange so action mutations refresh
                the Overview banner / tab badge in real time. */}
            {activeTab === 'review' && projectId && (
              <div>
                <h2 className="text-xl font-semibold text-gray-900 mb-6">
                  {t('detail.review')}
                </h2>
                <ReviewQueueView
                  projectId={projectId}
                  onCountChange={setPendingReviewCount}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {emailModalData && (
        <EmailComposeModal
          paperName={emailModalData.paper_name}
          paperTitle={emailModalData.paper_title}
          authors={emailModalData.authors}
          emails={emailModalData.emails}
          artifactId={emailModalData.artifact_id}
          projectId={projectId!}
          onClose={() => setEmailModalData(null)}
          onSent={handleEmailSent}
        />
      )}

      {selectedScreenshot && (
        <div
          className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6"
          onClick={() => setSelectedScreenshot(null)}
        >
          <img
            src={selectedScreenshot}
            alt={t('detail.download_debug_screenshot_full')}
            className="max-w-full max-h-full rounded shadow-2xl border border-white/20"
          />
        </div>
      )}

      <InputModal
        isOpen={!!currentInputRequest}
        inputRequest={currentInputRequest || undefined}
        onSubmit={submitInputResponse}
        onClose={closeInputModal}
      />
    </div>
  );
}
