// Searchable, filterable table of paper artifacts with linked analyses and datasets.

import { lazy, Suspense, useState, useMemo, useRef, useEffect } from 'react';
import {
  Ban,
  Database,
  Dna,
  FileCheck,
  FileSearch,
  FileText,
  Hash,
  LockKeyhole,
  Mail,
  Pill,
  Search,
  ShieldAlert,
  Table2,
  Wrench,
  X,
  ArrowUp,
  ArrowDown,
} from 'lucide-react';
import type { DbArtifact, PaperTableRow, ArtifactTableRow, PaperCost } from '../types/database';
import { useTranslation } from '../hooks/useTranslation';

// Lazy-loaded: pulls in react-markdown + remark-gfm, only needed when user opens a file.
const FileViewerModal = lazy(() => import('./FileViewerModal').then(m => ({ default: m.FileViewerModal })));

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface ArtifactListProps {
  artifacts: DbArtifact[];
  artifactRows?: ArtifactTableRow[];
  projectCost?: import('../types/database').ProjectCost | null;
}

const cn = (...classes: (string | boolean | undefined)[]) => {
  return classes.filter(Boolean).join(' ');
};

export function ArtifactList({ artifacts, artifactRows, projectCost }: ArtifactListProps) {
  const { t } = useTranslation();
  const [activeFilter, setActiveFilter] = useState<'all' | 'has_analysis' | 'has_datasets'>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortBy, setSortBy] = useState<'date' | 'name' | 'status'>('date');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [openPopoverId, setOpenPopoverId] = useState<number | null>(null);
  const [selectedArtifact, setSelectedArtifact] = useState<{ id: number; name: string } | null>(null);

  const getFileName = (filePath: string) => {
    const parts = filePath.split('/');
    return parts[parts.length - 1] || filePath;
  };

  const safeDecodeURIComponent = (value: string): string => {
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  };

  const isRemotePaperUrl = (filePath: string): boolean =>
    filePath.startsWith('http://') || filePath.startsWith('https://');

  /** Label for UI: decoded PDF title; uses DB file_name when present. */
  const getPaperDisplayName = (paper: DbArtifact, rawBasename: string): string => {
    const trimmedName = paper.file_name?.trim();
    if (trimmedName) {
      return safeDecodeURIComponent(trimmedName);
    }
    if (isRemotePaperUrl(paper.file_path)) {
      try {
        const pathname = new URL(paper.file_path).pathname;
        const seg = pathname.split('/').filter(Boolean).pop() || '';
        return seg ? safeDecodeURIComponent(seg) : rawBasename;
      } catch {
        return safeDecodeURIComponent(rawBasename);
      }
    }
    if (/%[0-9A-Fa-f]{2}/.test(rawBasename)) {
      return safeDecodeURIComponent(rawBasename);
    }
    return rawBasename;
  };

  const matchAnalysisToPaper = (paperFileName: string, analysisFileName: string): boolean => {
    // Exact stem match only. The previous `startsWith` rule mis-paired
    // pairs like "Yi2024.pdf" ⇒ "Yi2024_2.md"; analyses always carry their
    // paper's exact stem (either as `<stem>.md` or `<stem>_analysis.md`,
    // both normalized below), so a stricter equality is correct.
    const paperBase = paperFileName.replace(/\.pdf$/i, '');
    const analysisBase = analysisFileName.replace(/\.md$/i, '').replace(/_analysis$/i, '');
    return paperBase === analysisBase;
  };

  const normalizeArtifactKey = (value: string): string => {
    const decoded = safeDecodeURIComponent(value || '');
    return decoded
      .replace(/\.[^/.]+$/i, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '');
  };

  const extractDatasetGroupFromPath = (datasetPath: string): string | null => {
    const normalizedPath = datasetPath.replace(/\\/g, '/');
    const explicitMatch = normalizedPath.match(/(?:^|\/)outputs\/datasets\/([^/]+)\//i);
    if (explicitMatch?.[1]) {
      return explicitMatch[1];
    }

    const datasetsMatch = normalizedPath.match(/(?:^|\/)datasets\/([^/]+)\//i);
    if (datasetsMatch?.[1]) {
      return datasetsMatch[1];
    }

    const segments = normalizedPath.split('/').filter(Boolean);
    if (segments.length >= 2) {
      return segments[segments.length - 2];
    }
    return null;
  };

  const getAnalysisBaseName = (analysisPath: string): string => {
    const analysisFileName = getFileName(analysisPath);
    return analysisFileName.replace(/\.md$/i, '').replace(/_analysis$/i, '');
  };

  const buildTableData = (artifacts: DbArtifact[]): PaperTableRow[] => {
    const papers = artifacts.filter(a => a.artifact_type === 'paper');
    const analyses = artifacts.filter(a => a.artifact_type === 'analysis');
    const datasets = artifacts.filter(
      a => a.artifact_type === 'dataset'
        || a.artifact_type === 'embedded_dataset'
        || a.artifact_type === 'acquisition_evidence'
    );

    const datasetsByPaper = new Map<string, DbArtifact[]>();
    datasets.forEach(dataset => {
      const groupName = extractDatasetGroupFromPath(dataset.file_path);
      const groupKey = groupName ? normalizeArtifactKey(groupName) : '';
      if (groupKey) {
        if (!datasetsByPaper.has(groupKey)) datasetsByPaper.set(groupKey, []);
        datasetsByPaper.get(groupKey)!.push(dataset);
      }
    });

    const analysisEntries = analyses.map(analysis => ({
      analysis,
      baseName: getAnalysisBaseName(analysis.file_path),
    }));
    const usedAnalysisIds = new Set<number>();

    const rows = papers.map(paper => {
      const paperFileName = getFileName(paper.file_path);
      const paperDisplayName = getPaperDisplayName(paper, paperFileName);
      const paperBase = paperFileName.replace(/\.pdf$/i, '');
      const candidateAnalyses = analysisEntries.filter(
        entry => !usedAnalysisIds.has(entry.analysis.id)
      );

      let matchedEntry = candidateAnalyses.find(entry =>
        matchAnalysisToPaper(paperFileName, getFileName(entry.analysis.file_path))
      );
      if (!matchedEntry && papers.length === 1 && analyses.length === 1) {
        matchedEntry = analysisEntries[0];
      }
      // No "closest-by-created_at" fallback. Previously this cascaded:
      // a paper without its own analysis (e.g. Enkhee2024 returned
      // no_suitable_data) would steal the closest available analysis,
      // shifting every subsequent paper one slot and leaving the last
      // paper (often the oldest, e.g. Marc2015_2) showing "待分析" with
      // an undefined match. Without that hack, an analysis-less paper
      // correctly renders as 'pending' and the rest keep their pairs.

      const matchedAnalysis = matchedEntry?.analysis;
      if (matchedAnalysis) {
        usedAnalysisIds.add(matchedAnalysis.id);
      }

      // Two-state by product decision: an analysis markdown row existing
      // for this paper ⇒ 'ready', regardless of has_content or downstream
      // usefulness. Pipelines that produced manual_required / no_suitable
      // are still reflected in dataClassificationFlag and the review queue.
      const analysisStatus: 'pending' | 'ready' = matchedAnalysis ? 'ready' : 'pending';

      const datasetLookupKey = normalizeArtifactKey(matchedEntry?.baseName || paperBase);
      let paperDatasets = datasetsByPaper.get(datasetLookupKey) || [];
      if (paperDatasets.length === 0 && papers.length === 1 && datasets.length > 0) {
        paperDatasets = datasets;
      }
      const downloadableDatasets = paperDatasets.filter(d => d.artifact_type === 'dataset');
      const embeddedDatasets = paperDatasets.filter(d => d.artifact_type === 'embedded_dataset');
      const acquisitionEvidence = paperDatasets.filter(d => d.artifact_type === 'acquisition_evidence');

      // Qualify verdict for the paper: highest grade across its dataset units
      // (qualify writes the same verdict to a unit's members; a paper is one unit).
      const QUAL_ORDER = ['strict', 'loose', 'fail', 'processing', 'error', 'pending'];
      const qualSet = new Set(
        [...downloadableDatasets, ...embeddedDatasets]
          .map(d => d.qualification_status)
          .filter((s): s is string => Boolean(s))
      );
      const qualificationStatus = QUAL_ORDER.find(s => qualSet.has(s));
      const qualificationReason = [...downloadableDatasets, ...embeddedDatasets]
        .find(d => d.qualification_status === qualificationStatus && d.qualification_reason)
        ?.qualification_reason;

      return {
        paperId: paper.id,
        paperFileName,
        paperDisplayName,
        paperFilePath: paper.file_path,
        paperIsRemoteUrl: isRemotePaperUrl(paper.file_path),
        paperFileSize: paper.file_size,
        paperCreatedAt: paper.created_at,
        paperHasContent: paper.has_content === 1,
        analysisId: matchedAnalysis?.id,
        analysisStatus,
        analysisFileSize: matchedAnalysis?.file_size,
        analysisHasContent: matchedAnalysis?.has_content === 1,
        dataClassificationFlag: matchedAnalysis?.data_classification_flag,
        datasetCount: downloadableDatasets.length,
        embeddedDatasetCount: embeddedDatasets.length,
        evidenceCount: acquisitionEvidence.length,
        qualificationStatus,
        qualificationReason,
        datasets: downloadableDatasets.map(d => ({
          id: d.id,
          fileName: getFileName(d.file_path),
          filePath: d.file_path,
          fileSize: d.file_size,
          createdAt: d.created_at,
          artifactType: d.artifact_type,
          acquisitionSource: d.acquisition_source,
          acquisitionStatus: d.acquisition_status,
          trustLevel: d.trust_level,
        })),
        embeddedDatasets: embeddedDatasets.map(d => ({
          id: d.id,
          fileName: getFileName(d.file_path),
          filePath: d.file_path,
          fileSize: d.file_size,
          createdAt: d.created_at,
          artifactType: d.artifact_type,
          acquisitionSource: d.acquisition_source,
          acquisitionStatus: d.acquisition_status,
          trustLevel: d.trust_level,
        })),
        acquisitionEvidence: acquisitionEvidence.map(d => ({
          id: d.id,
          fileName: getFileName(d.file_path),
          filePath: d.file_path,
          fileSize: d.file_size,
          createdAt: d.created_at,
          artifactType: d.artifact_type,
          acquisitionSource: d.acquisition_source,
          acquisitionStatus: d.acquisition_status,
          trustLevel: d.trust_level,
        })),
      };
    });

    return rows;
  };

  const tableData = useMemo(
    () => (artifactRows && artifactRows.length > 0 ? artifactRows : buildTableData(artifacts)),
    [artifactRows, artifacts]
  );

  const filteredData = useMemo(() => {
    let filtered = tableData;

    if (activeFilter === 'has_analysis') {
      filtered = filtered.filter(row => row.analysisStatus === 'ready');
    } else if (activeFilter === 'has_datasets') {
      filtered = filtered.filter(
        row => row.datasetCount > 0 || row.embeddedDatasetCount > 0
      );
    }

    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(row =>
        row.paperDisplayName.toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [tableData, activeFilter, searchQuery]);

  const sortedData = useMemo(() => {
    const sorted = [...filteredData];
    sorted.sort((a, b) => {
      let compareValue = 0;
      if (sortBy === 'name') {
        compareValue = a.paperDisplayName.localeCompare(b.paperDisplayName);
      } else if (sortBy === 'date') {
        compareValue = new Date(a.paperCreatedAt).getTime() - new Date(b.paperCreatedAt).getTime();
      } else if (sortBy === 'status') {
        const statusOrder: Record<'ready' | 'pending', number> = { ready: 0, pending: 1 };
        compareValue = statusOrder[a.analysisStatus] - statusOrder[b.analysisStatus];
      }
      return sortOrder === 'asc' ? compareValue : -compareValue;
    });
    return sorted;
  }, [filteredData, sortBy, sortOrder]);

  const renderAnalysisBadge = (row: PaperTableRow) => {
    const getBadgeStyle = () => {
      switch (row.analysisStatus) {
        case 'pending':
          return 'bg-gray-100 text-gray-600 border-gray-300';
        case 'ready':
          return 'bg-green-100 text-green-700 border-green-300 cursor-pointer hover:bg-green-200';
      }
    };

    const handleClick = () => {
      if (row.analysisStatus === 'ready' && row.analysisId) {
        setSelectedArtifact({
          id: row.analysisId,
          name: row.paperDisplayName.replace(/\.pdf$/i, '_analysis.md')
        });
      }
    };

    return (
      <button
        onClick={handleClick}
        disabled={row.analysisStatus !== 'ready'}
        className={cn(
          "inline-flex items-center space-x-1 px-3 py-1 rounded-full text-xs font-medium border transition-colors",
          getBadgeStyle()
        )}
      >
        {row.analysisStatus === 'pending' && (
          <>
            <span className="w-2 h-2 rounded-full bg-gray-400"></span>
            <span>{t('artifacts.no_analysis')}</span>
          </>
        )}
        {row.analysisStatus === 'ready' && (
          <>
            <FileCheck className="w-3 h-3" />
            <span>{t('artifacts.analysis')}</span>
          </>
        )}
      </button>
    );
  };

  const classifyDownloadOutcome = (row: PaperTableRow): string => {
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

    return row.dataClassificationFlag || 'no_analysis';
  };

  const renderDataFlagBadge = (flag?: string) => {
    if (!flag || flag === 'no_analysis') {
      return <span className="text-sm text-gray-400">-</span>;
    }

    const configs: Record<string, {
      label: string;
      bgColor: string;
      textColor: string;
      borderColor: string;
      tooltip: string;
    }> = {
      both_data: {
        label: t('artifacts.both_data'),
        bgColor: 'bg-green-100',
        textColor: 'text-green-700',
        borderColor: 'border-green-300',
        tooltip: t('artifacts.both_tooltip')
      },
      sequencing_only: {
        label: t('artifacts.seq_only'),
        bgColor: 'bg-yellow-100',
        textColor: 'text-yellow-700',
        borderColor: 'border-yellow-300',
        tooltip: t('artifacts.seq_tooltip')
      },
      drug_only: {
        label: t('artifacts.drug_only'),
        bgColor: 'bg-yellow-100',
        textColor: 'text-yellow-700',
        borderColor: 'border-yellow-300',
        tooltip: t('artifacts.drug_tooltip')
      },
      manual_required: {
        label: t('artifacts.manual'),
        bgColor: 'bg-orange-100',
        textColor: 'text-orange-700',
        borderColor: 'border-orange-300',
        tooltip: t('artifacts.manual_tooltip')
      },
      contact_author: {
        label: t('artifacts.contact'),
        bgColor: 'bg-gray-100',
        textColor: 'text-gray-600',
        borderColor: 'border-gray-300',
        tooltip: t('artifacts.contact_tooltip')
      },
      repository_dataset: {
        label: t('data.repository_dataset'),
        bgColor: 'bg-blue-100',
        textColor: 'text-blue-700',
        borderColor: 'border-blue-300',
        tooltip: t('artifacts.repository_dataset_tooltip')
      },
      embedded_dataset: {
        label: t('data.embedded_dataset'),
        bgColor: 'bg-amber-100',
        textColor: 'text-amber-700',
        borderColor: 'border-amber-300',
        tooltip: t('artifacts.embedded_dataset_tooltip')
      },
      acquisition_evidence: {
        label: t('data.acquisition_evidence'),
        bgColor: 'bg-slate-100',
        textColor: 'text-slate-700',
        borderColor: 'border-slate-300',
        tooltip: t('artifacts.acquisition_evidence_tooltip')
      },
      controlled_access: {
        label: t('data.controlled_access'),
        bgColor: 'bg-red-100',
        textColor: 'text-red-700',
        borderColor: 'border-red-300',
        tooltip: t('artifacts.controlled_access_tooltip')
      },
      browser_blocked: {
        label: t('data.browser_blocked'),
        bgColor: 'bg-red-100',
        textColor: 'text-red-700',
        borderColor: 'border-red-300',
        tooltip: t('artifacts.browser_blocked_tooltip')
      },
      accession_missing: {
        label: t('data.accession_missing'),
        bgColor: 'bg-orange-100',
        textColor: 'text-orange-700',
        borderColor: 'border-orange-300',
        tooltip: t('artifacts.accession_missing_tooltip')
      },
      pdf_only: {
        label: t('data.pdf_only'),
        bgColor: 'bg-gray-100',
        textColor: 'text-gray-700',
        borderColor: 'border-gray-300',
        tooltip: t('artifacts.pdf_only_tooltip')
      },
      no_valid_dataset: {
        label: t('data.no_valid_dataset'),
        bgColor: 'bg-gray-100',
        textColor: 'text-gray-700',
        borderColor: 'border-gray-300',
        tooltip: t('artifacts.no_valid_dataset_tooltip')
      }
    };

    const config = configs[flag];
    if (!config) {
      return <span className="text-sm text-gray-400">-</span>;
    }
    const iconMap = {
      both_data: FileCheck,
      sequencing_only: Dna,
      drug_only: Pill,
      manual_required: Wrench,
      contact_author: Mail,
      repository_dataset: Database,
      embedded_dataset: Table2,
      acquisition_evidence: FileSearch,
      controlled_access: LockKeyhole,
      browser_blocked: ShieldAlert,
      accession_missing: Hash,
      pdf_only: FileText,
      no_valid_dataset: Ban,
    } as const;
    const Icon = iconMap[flag as keyof typeof iconMap];

    return (
      <div
        className={cn(
          "inline-flex items-center space-x-1 px-2 py-1 rounded-full text-xs font-medium border",
          config.bgColor,
          config.textColor,
          config.borderColor
        )}
        title={config.tooltip}
      >
        {Icon && <Icon className="w-3 h-3 shrink-0" />}
        <span>{config.label}</span>
      </div>
    );
  };

  const getAcquisitionSourceLabel = (source?: string) => {
    switch (source) {
      case 'repository_download':
        return 'Repository';
      case 'supplementary_download':
        return 'Supplement';
      case 'author_attachment':
        return 'Author';
      case 'paper_table_extraction':
        return 'Paper Table';
      case 'paper_figure_extraction':
        return 'Figure';
      case 'contact_author':
        return 'Author Contact';
      case 'repository_evidence':
        return 'Evidence';
      default:
        return source || 'Unknown';
    }
  };

  const getAcquisitionStatusLabel = (status?: string) => {
    switch (status) {
      case 'completed':
        return 'Completed';
      case 'partial':
        return 'Partial';
      case 'awaiting_external':
        return 'Awaiting Reply';
      case 'pending':
        return 'Pending';
      case 'failed':
        return 'Failed';
      case 'skipped':
        return 'Skipped';
      default:
        return status || 'Unknown';
    }
  };

  const renderMetaBadge = (
    label: string,
    tone: 'blue' | 'amber' | 'slate' | 'green' | 'red' | 'gray' = 'gray'
  ) => {
    const toneMap = {
      blue: 'bg-blue-100 text-blue-700 border-blue-200',
      amber: 'bg-amber-100 text-amber-700 border-amber-200',
      slate: 'bg-slate-100 text-slate-700 border-slate-200',
      green: 'bg-green-100 text-green-700 border-green-200',
      red: 'bg-red-100 text-red-700 border-red-200',
      gray: 'bg-gray-100 text-gray-700 border-gray-200',
    };
    return (
      <span
        className={cn(
          'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium',
          toneMap[tone]
        )}
      >
        {label}
      </span>
    );
  };

  const renderTrustBadge = (trustLevel?: 'high' | 'medium' | 'low') => {
    if (!trustLevel) return null;
    const tone = trustLevel === 'high' ? 'green' : trustLevel === 'medium' ? 'amber' : 'gray';
    return renderMetaBadge(`${trustLevel} trust`, tone);
  };

  const formatCostUsd = (usd: number): string => {
    if (!usd) return '$0';
    if (usd < 0.01) return `$${usd.toFixed(4)}`;
    return `$${usd.toFixed(2)}`;
  };

  const renderQualificationBadge = (status?: string, reason?: string | null) => {
    if (!status) return <span className="text-gray-400 text-xs">{t('artifacts.qual_none')}</span>;
    const toneMap: Record<string, 'green' | 'blue' | 'red' | 'amber' | 'gray'> = {
      strict: 'green', loose: 'blue', fail: 'red', processing: 'amber',
      error: 'gray', pending: 'gray',
    };
    const badge = renderMetaBadge(t(`artifacts.qual_${status}`), toneMap[status] || 'gray');
    if (!reason) return badge;
    return (
      <span className="cursor-help" title={reason}>
        {badge}
      </span>
    );
  };

  const renderCostCell = (cost?: PaperCost | null) => {
    if (!cost || !cost.totalCostUsd) {
      return <span className="text-gray-400 text-xs">-</span>;
    }
    const stageLabels: Record<string, string> = {
      prescreen: t('artifacts.cost_prescreen'),
      analyze: t('artifacts.cost_analyze'),
      download: t('artifacts.cost_download'),
      qualify: t('artifacts.cost_qualify'),
    };
    const breakdown = ['prescreen', 'analyze', 'download', 'qualify']
      .filter(s => cost.stages[s])
      .map(s => `${stageLabels[s] || s}: ${formatCostUsd(cost.stages[s].costUsd)} (${cost.stages[s].inputTokens + cost.stages[s].outputTokens} tok)`)
      .join('\n');
    return (
      <span
        className="text-sm font-medium text-gray-700 cursor-help"
        title={`${breakdown}\n${t('artifacts.cost_tokens')}: ${cost.totalInputTokens + cost.totalOutputTokens}`}
      >
        {formatCostUsd(cost.totalCostUsd)}
      </span>
    );
  };

  const renderArtifactMeta = (artifact: {
    artifactType?: string;
    acquisitionSource?: string;
    acquisitionStatus?: string;
    trustLevel?: 'high' | 'medium' | 'low';
  }) => (
    <div className="flex flex-wrap gap-1 mt-1">
      {artifact.acquisitionSource && renderMetaBadge(
        getAcquisitionSourceLabel(artifact.acquisitionSource),
        artifact.artifactType === 'dataset'
          ? 'blue'
          : artifact.artifactType === 'embedded_dataset'
            ? 'amber'
            : 'slate'
      )}
      {artifact.acquisitionStatus && renderMetaBadge(
        getAcquisitionStatusLabel(artifact.acquisitionStatus),
        artifact.acquisitionStatus === 'completed'
          ? 'green'
          : artifact.acquisitionStatus === 'failed'
            ? 'red'
            : 'gray'
      )}
      {renderTrustBadge(artifact.trustLevel)}
    </div>
  );

  const renderDatasetsCell = (row: PaperTableRow) => {
    const dataArtifactCount = row.datasetCount + row.embeddedDatasetCount;
    if (dataArtifactCount === 0 && row.evidenceCount === 0) {
      return <span className="text-sm text-gray-400">-</span>;
    }

    if (dataArtifactCount === 0) {
      return (
        <div className="relative">
          <button
            onClick={() => setOpenPopoverId(
              openPopoverId === row.paperId ? null : row.paperId
            )}
            className="text-sm text-slate-600 hover:text-slate-700 font-medium"
          >
            {t('artifacts.evidence_only')}
          </button>

          <DatasetPopover
            datasets={row.datasets}
            embeddedDatasets={row.embeddedDatasets}
            acquisitionEvidence={row.acquisitionEvidence}
            isOpen={openPopoverId === row.paperId}
            onClose={() => setOpenPopoverId(null)}
          />
        </div>
      );
    }

    return (
      <div className="relative">
        <button
          onClick={() => setOpenPopoverId(
            openPopoverId === row.paperId ? null : row.paperId
          )}
          className="text-sm text-blue-600 hover:text-blue-700 font-medium"
        >
          {dataArtifactCount} {dataArtifactCount > 1 ? t('artifacts.files') : t('artifacts.file')}
        </button>

        <DatasetPopover
          datasets={row.datasets}
          embeddedDatasets={row.embeddedDatasets}
          acquisitionEvidence={row.acquisitionEvidence}
          isOpen={openPopoverId === row.paperId}
          onClose={() => setOpenPopoverId(null)}
        />
      </div>
    );
  };

  const DatasetPopover: React.FC<{
    datasets: PaperTableRow['datasets'];
    embeddedDatasets: PaperTableRow['embeddedDatasets'];
    acquisitionEvidence: PaperTableRow['acquisitionEvidence'];
    isOpen: boolean;
    onClose: () => void;
  }> = ({ datasets, embeddedDatasets, acquisitionEvidence, isOpen, onClose }) => {
    const [localSelected, setLocalSelected] = useState<Set<number>>(new Set());
    const popoverRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
      if (!isOpen) return;
      const handleClickOutside = (e: MouseEvent) => {
        if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) onClose();
      };
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [isOpen, onClose]);

    useEffect(() => {
      if (isOpen) setLocalSelected(new Set());
    }, [isOpen]);

    if (!isOpen) return null;

    const downloadableItems = [...datasets, ...embeddedDatasets];
    const allSelected = localSelected.size === downloadableItems.length && downloadableItems.length > 0;
    const someSelected = localSelected.size > 0 && localSelected.size < downloadableItems.length;

    const handleToggleAll = () => {
      if (allSelected) {
        setLocalSelected(new Set());
      } else {
        setLocalSelected(new Set(downloadableItems.map(d => d.id)));
      }
    };

    const handleToggleDataset = (id: number) => {
      const newSelected = new Set(localSelected);
      if (newSelected.has(id)) {
        newSelected.delete(id);
      } else {
        newSelected.add(id);
      }
      setLocalSelected(newSelected);
    };

    const handleDownload = () => {
      const selectedDatasetsList = downloadableItems.filter(d => localSelected.has(d.id));
      selectedDatasetsList.forEach((dataset, index) => {
        setTimeout(() => {
          const iframe = document.createElement('iframe');
          iframe.style.display = 'none';
          iframe.src = `${API_URL}/api/database/artifacts/${dataset.id}/download?token=${sessionStorage.getItem('auth_token')}`;
          document.body.appendChild(iframe);
          setTimeout(() => document.body.removeChild(iframe), 5000);
        }, index * 150);
      });
      onClose();
    };

    return (
      <>
        {/* xs backdrop — visual focus on the modal; click-outside is already
            handled by the popoverRef listener */}
        <div className="fixed inset-0 z-40 bg-black/40 sm:hidden" aria-hidden />
        <div
          ref={popoverRef}
          className={cn(
            // xs: viewport-centered modal
            "fixed left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-[calc(100vw-2rem)] max-w-md max-h-[85vh] overflow-y-auto z-50",
            // sm+: popover anchored to trigger
            "sm:absolute sm:left-auto sm:top-full sm:right-0 sm:translate-x-0 sm:translate-y-0 sm:mt-1 sm:w-80 sm:max-h-none",
            "bg-white border border-gray-300 rounded-lg shadow-lg"
          )}
          onMouseDown={(e) => e.stopPropagation()}
        >
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
          <label className="flex items-center space-x-2 cursor-pointer">
            <input
              type="checkbox"
              checked={allSelected}
              ref={(el) => {
                if (el) el.indeterminate = someSelected;
              }}
              onChange={handleToggleAll}
              className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 cursor-pointer"
            />
            <h4 className="font-medium text-gray-900">
              {t('artifacts.datasets')} ({datasets.length + embeddedDatasets.length})
            </h4>
          </label>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="max-h-[260px] overflow-y-auto">
          {downloadableItems.length === 0 && (
            <div className="px-4 py-3 text-sm text-slate-600 border-b border-gray-100">
              {t('artifacts.no_downloadable_datasets')}
            </div>
          )}
          {datasets.map(dataset => (
            <label
              key={dataset.id}
              className="flex items-center px-4 py-2 hover:bg-gray-50 border-b border-gray-100 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={localSelected.has(dataset.id)}
                onChange={() => handleToggleDataset(dataset.id)}
                className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 flex-shrink-0 cursor-pointer"
              />
              <div className="flex items-center space-x-2 flex-1 min-w-0 ml-3">
                <Database className="w-4 h-4 text-purple-600 flex-shrink-0" />
                <div className="min-w-0">
                  <span className="text-sm text-gray-700 truncate block" title={dataset.fileName}>
                    {dataset.fileName}
                  </span>
                  {renderArtifactMeta(dataset)}
                </div>
              </div>
            </label>
          ))}
          {embeddedDatasets.length > 0 && (
            <div className="px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-y border-gray-100">
              {t('artifacts.embedded_datasets')}
            </div>
          )}
          {embeddedDatasets.map(dataset => (
            <label
              key={dataset.id}
              className="flex items-center px-4 py-2 hover:bg-gray-50 border-b border-gray-100 cursor-pointer"
            >
              <input
                type="checkbox"
                checked={localSelected.has(dataset.id)}
                onChange={() => handleToggleDataset(dataset.id)}
                className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500 flex-shrink-0 cursor-pointer"
              />
              <div className="flex items-center space-x-2 flex-1 min-w-0 ml-3">
                <Database className="w-4 h-4 text-amber-600 flex-shrink-0" />
                <div className="min-w-0">
                  <span className="text-sm text-gray-700 truncate block" title={dataset.fileName}>
                    {dataset.fileName}
                  </span>
                  {renderArtifactMeta(dataset)}
                </div>
              </div>
            </label>
          ))}
          {acquisitionEvidence.length > 0 && (
            <div className="px-4 py-2 text-xs font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-y border-gray-100">
              {t('artifacts.evidence')}
            </div>
          )}
          {acquisitionEvidence.map(dataset => (
            <button
              key={dataset.id}
              type="button"
              onClick={() => {
                setSelectedArtifact({ id: dataset.id, name: dataset.fileName });
                onClose();
              }}
              className="flex items-center w-full px-4 py-2 hover:bg-gray-50 border-b border-gray-100"
            >
              <div className="flex items-center space-x-2 flex-1 min-w-0">
                <FileText className="w-4 h-4 text-slate-600 flex-shrink-0" />
                <div className="min-w-0">
                  <span className="text-sm text-gray-700 truncate text-left block" title={dataset.fileName}>
                    {dataset.fileName}
                  </span>
                  {renderArtifactMeta(dataset)}
                </div>
              </div>
            </button>
          ))}
        </div>

        <div className="px-4 py-3 border-t border-gray-200 bg-gray-50">
          <button
            onClick={handleDownload}
            disabled={localSelected.size === 0}
            className={cn(
              "w-full px-4 py-2 rounded-md transition-colors text-sm font-medium",
              localSelected.size > 0
                ? "bg-blue-600 text-white hover:bg-blue-700"
                : "bg-gray-200 text-gray-500 cursor-not-allowed"
            )}
          >
            {t('artifacts.download')}
            {localSelected.size > 0
              ? ` (${localSelected.size} ${t('artifacts.selected')})`
              : ''}
          </button>
        </div>
        </div>
      </>
    );
  };

  if (artifacts.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        {t('artifacts.none_for_project')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {projectCost && projectCost.total.totalCostUsd > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm">
          <span className="font-semibold text-blue-900">
            {t('artifacts.total_cost')}: {formatCostUsd(projectCost.total.totalCostUsd)}
          </span>
          <span className="text-blue-700 text-xs">
            {projectCost.total.totalInputTokens + projectCost.total.totalOutputTokens} {t('artifacts.cost_tokens')}
          </span>
          {(['prescreen', 'analyze', 'download', 'qualify'] as const)
            .filter((s) => projectCost.total.byStage[s])
            .map((s) => (
              <span key={s} className="text-blue-700 text-xs">
                {t(`artifacts.cost_${s}`)}: {formatCostUsd(projectCost.total.byStage[s].costUsd)}
              </span>
            ))}
        </div>
      )}
      <div className="space-y-3 p-3 sm:p-4 bg-gray-50 border border-gray-200 rounded-lg">
        {/* Row 1: search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('artifacts.search_placeholder')}
            className="w-full pl-10 pr-10 py-2 border border-gray-300 rounded-md focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 text-sm"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Row 2: filters (left) + sort + direction (right via ml-auto) */}
        <div className="flex flex-wrap items-center gap-2">
          {(['all', 'has_analysis', 'has_datasets'] as const).map((filter) => (
            <button
              key={filter}
              onClick={() => setActiveFilter(filter)}
              className={cn(
                "px-3 py-1 rounded-full text-sm font-medium transition-colors whitespace-nowrap",
                activeFilter === filter
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-700 hover:bg-gray-200"
              )}
            >
              {filter === 'all' ? t('artifacts.filter.all') : filter === 'has_analysis' ? t('artifacts.filter.has_analysis') : t('artifacts.filter.has_datasets')}
            </button>
          ))}
          <div className="flex items-center gap-2 ml-auto">
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as any)}
              className="px-2 py-1.5 border border-gray-300 rounded-md text-sm bg-white focus:ring-2 focus:ring-blue-500"
            >
              <option value="date">{t('artifacts.date')}</option>
              <option value="name">{t('artifacts.name')}</option>
              <option value="status">{t('artifacts.status')}</option>
            </select>
            <button
              onClick={() => setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')}
              className="p-1.5 border border-gray-300 rounded-md hover:bg-gray-50 transition-colors bg-white"
              title={sortOrder === 'asc' ? t('artifacts.sort.asc') : t('artifacts.sort.desc')}
            >
              {sortOrder === 'asc' ? <ArrowUp className="w-4 h-4" /> : <ArrowDown className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </div>

      <div className="hidden lg:block border border-gray-200 rounded-lg overflow-y-auto min-h-[400px] max-h-[600px] artifacts-table-scrollbar">
        <div className="grid grid-cols-[minmax(250px,2fr)_160px_200px_200px_140px_120px] gap-4 p-4 bg-gray-50 border-b border-gray-200 font-semibold text-gray-700 text-sm sticky top-0 z-10">
          <div>{t('artifacts.paper')}</div>
          <div>{t('artifacts.data_type')}</div>
          <div>{t('artifacts.analysis_status')}</div>
          <div>{t('artifacts.datasets')}</div>
          <div>{t('artifacts.qualification')}</div>
          <div>{t('artifacts.cost')}</div>
        </div>

        {sortedData.map(row => (
          <div
            key={row.paperId}
            className="grid grid-cols-[minmax(250px,2fr)_160px_200px_200px_140px_120px] gap-4 p-4 border-b border-gray-100 hover:bg-gray-50 transition-colors"
          >
              <div className="flex items-center space-x-2 min-w-0">
                <FileText className="w-4 h-4 text-blue-600 flex-shrink-0" />
                {row.paperIsRemoteUrl ? (
                  <a
                    href={row.paperFilePath}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm font-medium truncate text-left text-blue-600 hover:text-blue-700 hover:underline"
                    title={row.paperDisplayName}
                  >
                    {row.paperDisplayName}
                  </a>
                ) : (
                  <button
                    onClick={() => row.paperHasContent && setSelectedArtifact({
                      id: row.paperId,
                      name: row.paperFileName
                    })}
                    className={cn(
                      "text-sm font-medium truncate text-left",
                      row.paperHasContent
                        ? "text-blue-600 hover:text-blue-700 cursor-pointer"
                        : "text-gray-400 cursor-not-allowed"
                    )}
                    title={row.paperDisplayName}
                    disabled={!row.paperHasContent}
                  >
                    {row.paperDisplayName}
                  </button>
                )}
              </div>

              <div className="flex items-center">
                {renderDataFlagBadge(classifyDownloadOutcome(row))}
              </div>

              <div className="flex items-center">
                {renderAnalysisBadge(row)}
              </div>

              <div className="flex items-center">
                <div className="space-y-1">
                  {renderDatasetsCell(row)}
                  {(row.embeddedDatasetCount > 0 || row.evidenceCount > 0) && (
                    <div className="flex flex-wrap gap-1">
                      {row.embeddedDatasetCount > 0 && renderMetaBadge(`Embedded ${row.embeddedDatasetCount}`, 'amber')}
                      {row.evidenceCount > 0 && renderMetaBadge(`Evidence ${row.evidenceCount}`, 'slate')}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-center">
                {renderQualificationBadge(row.qualificationStatus, row.qualificationReason)}
              </div>

              <div className="flex items-center">
                {renderCostCell(row.cost)}
              </div>
          </div>
        ))}
      </div>

      <div className="block lg:hidden space-y-3">
        {sortedData.map(row => (
          <div key={row.paperId} className="border border-gray-200 rounded-lg p-4 bg-white">
            <div className="flex items-center space-x-2 mb-3">
              <FileText className="w-4 h-4 text-blue-600 flex-shrink-0" />
              {row.paperIsRemoteUrl ? (
                <a
                  href={row.paperFilePath}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-medium truncate text-left flex-1 text-blue-600 hover:text-blue-700 hover:underline"
                  title={row.paperDisplayName}
                >
                  {row.paperDisplayName}
                </a>
              ) : (
                <button
                  onClick={() => row.paperHasContent && setSelectedArtifact({
                    id: row.paperId,
                    name: row.paperFileName
                  })}
                  className={cn(
                    "text-sm font-medium truncate text-left flex-1",
                    row.paperHasContent
                      ? "text-blue-600 hover:text-blue-700"
                      : "text-gray-400"
                  )}
                  disabled={!row.paperHasContent}
                  title={row.paperDisplayName}
                >
                  {row.paperDisplayName}
                </button>
              )}
            </div>

            <div className="space-y-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-gray-500 text-xs shrink-0">{t('artifacts.data_type')}:</span>
                {renderDataFlagBadge(classifyDownloadOutcome(row))}
              </div>

              <div className="flex items-center justify-between gap-2">
                <span className="text-gray-500 text-xs shrink-0">{t('artifacts.analysis')}:</span>
                {renderAnalysisBadge(row)}
              </div>

              <div className="flex items-start justify-between gap-2">
                <span className="text-gray-500 text-xs shrink-0 mt-1">{t('artifacts.datasets')}:</span>
                <div className="flex flex-col items-end gap-1 min-w-0">
                  {renderDatasetsCell(row)}
                  {(row.embeddedDatasetCount > 0 || row.evidenceCount > 0) && (
                    <div className="flex flex-wrap gap-1 justify-end">
                      {row.embeddedDatasetCount > 0 && renderMetaBadge(`Embedded ${row.embeddedDatasetCount}`, 'amber')}
                      {row.evidenceCount > 0 && renderMetaBadge(`Evidence ${row.evidenceCount}`, 'slate')}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-center justify-between gap-2">
                <span className="text-gray-500 text-xs shrink-0">{t('artifacts.qualification')}:</span>
                {renderQualificationBadge(row.qualificationStatus, row.qualificationReason)}
              </div>

              <div className="flex items-center justify-between gap-2">
                <span className="text-gray-500 text-xs shrink-0">{t('artifacts.cost')}:</span>
                {renderCostCell(row.cost)}
              </div>
            </div>
          </div>
        ))}
      </div>

      {sortedData.length === 0 && (
        <div className="text-center py-12 text-gray-500">
          <p className="text-lg font-medium">{t('artifacts.no_matches')}</p>
          <p className="text-sm mt-2">{t('artifacts.adjust_filters')}</p>
        </div>
      )}

      {selectedArtifact && (
        <Suspense fallback={null}>
          <FileViewerModal
            artifactId={selectedArtifact.id}
            fileName={selectedArtifact.name}
            onClose={() => setSelectedArtifact(null)}
          />
        </Suspense>
      )}
    </div>
  );
}
