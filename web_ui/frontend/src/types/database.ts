// TypeScript types for database entities (persisted projects).

export interface DbProject {
  id: string;
  name: string;
  research_query: string;
  status: 'pending' | 'running' | 'paused' | 'cancelled' | 'completed' | 'failed';
  created_at: string;
  updated_at: string;
  analysis_model?: string | null;
  download_model?: string | null;
}

export interface DbStage {
  id: number;
  stage_name: 'find' | 'analyze' | 'download' | 'qualify';
  status: 'running' | 'completed' | 'failed';
  start_time: string;
  end_time?: string;
  duration_seconds?: number;
  error_message?: string;
}

export interface DbArtifact {
  id: number;
  artifact_type: 'paper' | 'analysis' | 'dataset' | 'embedded_dataset' | 'acquisition_evidence';
  file_path: string;
  file_name?: string;
  file_content?: string;
  file_size?: number;
  stage_name?: string;
  created_at: string;
  has_content?: number;
  data_classification_flag?: string;
  acquisition_source?: string;
  acquisition_status?: string;
  qualification_status?: string;
  qualification_reason?: string | null;
  trust_level?: 'high' | 'medium' | 'low';
  confidence?: number | null;
  produced_by?: string;
  provenance?: Record<string, unknown>;
}

export interface DbMessage {
  id: number;
  stage_name: string;
  team_name: string;
  content: string;
  message_type: 'info' | 'warning' | 'error';
  timestamp: string;
}

export interface DbExecutionLog {
  id: number;
  project_id: string;
  stage_name?: string;
  team_name?: string;
  event_type: string;
  message: string;
  severity: 'info' | 'warning' | 'error' | string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface FullProjectData {
  project: DbProject;
  stages: DbStage[];
  artifacts: DbArtifact[];
  messages: DbMessage[];
  // Count of unique papers the analyze stage has processed (including
  // no_suitable_data skips). Surfaced by the backend so the detail-page
  // badge matches the project-list badge — the raw artifacts filter alone
  // under-counts by the no_suitable_data papers (no .md saved). Optional
  // because old API responses won't carry it during a rolling deploy.
  analyze_processed_count?: number;
}

export interface StageCost {
  model?: string | null;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  durationMs: number;
}

export interface PaperCost {
  stages: Record<string, StageCost>;
  totalCostUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalDurationMs: number;
}

export interface ProjectCost {
  total: {
    totalCostUsd: number;
    totalInputTokens: number;
    totalOutputTokens: number;
    totalDurationMs: number;
    byStage: Record<string, { costUsd: number; inputTokens: number; outputTokens: number; durationMs: number }>;
  };
  perPaper: Array<Record<string, unknown>>;
}

export interface PaperTableRow {
  paperId: number;
  /** Raw basename from file_path (used for matching analyses/datasets). */
  paperFileName: string;
  /** Human-readable title (URL-decoded; prefer server file_name when set). */
  paperDisplayName: string;
  paperFilePath: string;
  paperIsRemoteUrl: boolean;
  paperFileSize?: number;
  paperCreatedAt: string;
  paperHasContent: boolean;

  analysisId?: number;
  // Two-state by product decision: 'ready' iff an analysis markdown
  // artifact exists for the paper (regardless of classification flag /
  // downstream usefulness), otherwise 'pending'. Finer outcomes live on
  // dataClassificationFlag and in the human review queue.
  analysisStatus: 'pending' | 'ready';
  analysisFileSize?: number;
  analysisHasContent: boolean;
  dataClassificationFlag?: string;

  datasetCount: number;
  embeddedDatasetCount: number;
  evidenceCount: number;
  qualificationStatus?: string;
  /** Decisive reason for the qualify verdict (esp. why it failed). */
  qualificationReason?: string | null;
  /** Per-paper LLM cost breakdown (analyze/download/qualify), null when none. */
  cost?: PaperCost | null;
  datasets: Array<{
    id: number;
    fileName: string;
    filePath: string;
    fileSize?: number;
    createdAt: string;
    artifactType?: string;
    acquisitionSource?: string;
    acquisitionStatus?: string;
    trustLevel?: 'high' | 'medium' | 'low';
  }>;
  embeddedDatasets: Array<{
    id: number;
    fileName: string;
    filePath: string;
    fileSize?: number;
    createdAt: string;
    artifactType?: string;
    acquisitionSource?: string;
    acquisitionStatus?: string;
    trustLevel?: 'high' | 'medium' | 'low';
  }>;
  acquisitionEvidence: Array<{
    id: number;
    fileName: string;
    filePath: string;
    fileSize?: number;
    createdAt: string;
    artifactType?: string;
    acquisitionSource?: string;
    acquisitionStatus?: string;
    trustLevel?: 'high' | 'medium' | 'low';
  }>;
}

export interface ArtifactTableRow extends PaperTableRow {}

export type DataClassificationFlag =
  | 'both_data'
  | 'sequencing_only'
  | 'drug_only'
  | 'manual_required'
  | 'contact_author'
  | 'no_analysis';
