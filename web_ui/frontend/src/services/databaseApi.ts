// Client for the /api/database endpoints (projects, artifacts, stages, messages, email).

import type {
  DbProject,
  FullProjectData,
  DbStage,
  DbArtifact,
  DbMessage,
  DbExecutionLog,
  ArtifactTableRow,
  ProjectCost,
} from '../types/database';

const API_BASE = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/database`;
const API_ROOT = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface ContactAuthorInfo {
  artifact_id: number;
  paper_name: string;
  paper_title: string;
  authors: string[];
  emails: string[];
  email_sent_at: string | null;
}

export interface SendEmailRequest {
  to: string[];
  subject: string;
  body: string;
  project_id: string;
  artifact_id?: number;
}

function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = sessionStorage.getItem('auth_token');
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

function handle401Response(response: Response) {
  if (response.status === 401) {
    sessionStorage.removeItem('auth_token');
    window.location.reload();
  }
}

export const databaseApi = {
  async getProjects(): Promise<DbProject[]> {
    const response = await fetch(`${API_BASE}/projects`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch projects: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getProjectFull(projectId: string): Promise<FullProjectData> {
    const response = await fetch(`${API_BASE}/projects/${projectId}/full`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch project details: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getProjectStages(projectId: string): Promise<DbStage[]> {
    const response = await fetch(`${API_BASE}/projects/${projectId}/stages`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch project stages: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getProjectArtifacts(projectId: string, artifactType?: string): Promise<DbArtifact[]> {
    const url = artifactType
      ? `${API_BASE}/projects/${projectId}/artifacts?artifact_type=${artifactType}`
      : `${API_BASE}/projects/${projectId}/artifacts`;
    const response = await fetch(url, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch project artifacts: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getProjectArtifactRows(projectId: string): Promise<ArtifactTableRow[]> {
    const response = await fetch(`${API_BASE}/projects/${projectId}/artifact-rows`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch artifact rows: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getProjectCost(projectId: string): Promise<ProjectCost> {
    const response = await fetch(`${API_BASE}/projects/${projectId}/cost`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch project cost: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getProjectMessages(projectId: string): Promise<DbMessage[]> {
    const response = await fetch(`${API_BASE}/projects/${projectId}/messages`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch project messages: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getProjectExecutionLogs(projectId: string, stageName?: string, limit = 1000): Promise<DbExecutionLog[]> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (stageName) {
      params.set('stage_name', stageName);
    }
    const response = await fetch(`${API_BASE}/projects/${projectId}/execution-logs?${params.toString()}`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch execution logs: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  getDownloadDebugImageUrl(projectId: string, imagePath: string): string {
    const token = sessionStorage.getItem('auth_token') || '';
    const params = new URLSearchParams({
      path: imagePath,
      token,
    });
    return `${API_BASE}/projects/${projectId}/download-debug-image?${params.toString()}`;
  },

  async getArtifactContent(artifactId: number): Promise<DbArtifact> {
    const response = await fetch(`${API_BASE}/artifacts/${artifactId}`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch artifact content: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async getArtifactContentText(artifactId: number): Promise<string> {
    const response = await fetch(`${API_BASE}/artifacts/${artifactId}/content`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch artifact content text: ${response.status} ${response.statusText}`);
    }
    const data = await response.json();
    return data.content;
  },

  async deleteProject(projectId: string): Promise<void> {
    const response = await fetch(`${API_BASE}/projects/${projectId}`, {
      method: 'DELETE',
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to delete project: ${response.status} ${response.statusText}`);
    }
  },

  async getContactAuthors(projectId: string): Promise<ContactAuthorInfo[]> {
    const response = await fetch(`${API_BASE}/projects/${projectId}/contact-authors`, {
      headers: getAuthHeaders(),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to fetch contact authors: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async generateEmail(artifactId: number, projectId: string): Promise<{ subject: string; body: string }> {
    const response = await fetch(`${API_ROOT}/api/email/generate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify({ artifact_id: artifactId, project_id: projectId }),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to generate email: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },

  async sendEmail(request: SendEmailRequest): Promise<{ success: boolean; message: string }> {
    const response = await fetch(`${API_ROOT}/api/email/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify(request),
    });
    handle401Response(response);
    if (!response.ok) {
      throw new Error(`Failed to send email: ${response.status} ${response.statusText}`);
    }
    return response.json();
  },
};
