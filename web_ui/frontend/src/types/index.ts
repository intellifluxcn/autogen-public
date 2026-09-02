// Shared type definitions for the AutoGen Web UI.

export enum StageStatus {
  PENDING = 'pending',
  IN_PROGRESS = 'in_progress',
  COMPLETED = 'completed',
  ERROR = 'error',
  CANCELLED = 'cancelled',
  PAUSED = 'paused',
}

/** Values accepted by GET /api/projects?status= (DB column, not StageStatus enum). */
export type ProjectListApiStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed';

export enum MessageType {
  SYSTEM = 'system',
  USER = 'user',
  ERROR = 'error',
  WARNING = 'warning',
  INFO = 'info',
  TEAM = 'team',
}

export enum PipelineStage {
  FIND = 'find',
  ANALYZE = 'analyze',
  DOWNLOAD = 'download',
  QUALIFY = 'qualify',
  COMPLETE = 'complete',
}

export interface Message {
  id: string;
  content: string;
  message_type: MessageType;
  team_name?: string;
  timestamp: string;
}

export interface StageInfo {
  stage: PipelineStage;
  status: StageStatus;
  progress: number;
  artifact_count?: number;
  start_time?: string;
  end_time?: string;
  messages: Message[];
  output_data?: Record<string, any>;
  error_message?: string;
}

export interface Project {
  id: string;
  name: string;
  query: string;
  created_at: string;
  updated_at: string;
  status: StageStatus;
  current_stage: PipelineStage;
  stages: Record<PipelineStage, StageInfo>;
  overall_progress: number;
  parallel_pipeline?: boolean;
  analysis_model?: string | null;
  download_model?: string | null;
}

export interface InputRequest {
  id: string;
  project_id: string;
  prompt: string;
  team_name?: string;
  timestamp: string;
  response?: string;
  responded_at?: string;
}

export interface WebSocketMessage {
  type: 'project_created' | 'project_updated' | 'project_deleted' |
        'stage_updated' | 'progress_updated' | 'message_added' |
        'input_requested' | 'input_response' | 'status_updated';
  project_id?: string;
  data: Record<string, any>;
}

export interface CreateProjectRequest {
  name: string;
  query: string;
  max_papers?: number;
  // parallel_pipeline removed in 2026-05 — the server reads PIPELINE_PARALLEL
  // from its environment as the single source of truth.
  analysis_model?: string;
  download_model?: string;
  // publication date range. YYYY/MM/DD (frontend converts
  // from HTML5 date input's YYYY-MM-DD before sending). Either empty/undef
  // → backend falls back to Python-level defaults in find/team.py.
  date_start?: string;
  date_end?: string;
  // B4 escape hatch — when true, analyze stage bypasses the
  // global analysis_cache and re-runs LLM for every paper. Default false.
  force_reanalyze?: boolean;
  // MeSH synonym expansion of search query. Default true.
  // Auto-bypassed by the backend when the query contains PubMed field tags
  // or explicit uppercase boolean operators.
  mesh_expansion?: boolean;
}

export interface UpdateProjectRequest {
  name?: string;
  query?: string;
}

export interface InputResponse {
  input_id: string;
  response: string;
}

export interface ProjectListResponse {
  items: Project[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProjectDashboardStats {
  pending: number;
  running: number;
  paused: number;
  completed: number;
  failed: number;
  active: number;
  in_progress: number;
}

export interface StageCardProps {
  project: Project;
  stage: PipelineStage;
  className?: string;
}

export interface ProjectGridProps {
  projects: Project[];
  onSelectProject?: (project: Project) => void;
  onPauseProject?: (projectId: string) => Promise<void>;
  onResumeProject?: (projectId: string) => Promise<void>;
  onCancelProject?: (projectId: string) => Promise<void>;
}

export interface InputModalProps {
  isOpen: boolean;
  inputRequest?: InputRequest;
  onSubmit: (response: string) => void;
  onClose: () => void;
}

export interface ProjectManagerProps {
  onCreateProject: (request: CreateProjectRequest) => void;
  isCreating?: boolean;
  projectStats: ProjectDashboardStats;
  search: string;
  onSearchChange: (value: string) => void;
  /** '' = all statuses (API omits param). Otherwise DB status string. */
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
  onRefresh: () => void;
  isRefreshing?: boolean;
}
