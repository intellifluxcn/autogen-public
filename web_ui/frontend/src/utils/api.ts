// REST API client with automatic auth header injection and 401 handling.

import {
  Project,
  CreateProjectRequest,
  UpdateProjectRequest,
  InputResponse,
  Message,
  ProjectListResponse,
  ProjectDashboardStats,
} from '../types';

const API_BASE_URL = `${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api`;

class ApiClient {
  private readonly baseUrl: string;

  constructor() {
    this.baseUrl = API_BASE_URL;
  }

  private getAuthHeaders(): Record<string, string> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    const token = sessionStorage.getItem('auth_token');
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    return headers;
  }

  get = async <T>(endpoint: string, signal?: AbortSignal): Promise<T> => {
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        method: 'GET',
        headers: this.getAuthHeaders(),
        signal,
      });

      if (response.status === 401) {
        sessionStorage.removeItem('auth_token');
        window.location.reload();
        throw new Error('Authentication expired. Please log in again.');
      }

      if (!response.ok) {
        throw new Error(`API request failed: ${response.status} ${response.statusText}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        // Browser fetch threw TypeError — could be either the backend being
        // down OR a CORS preflight rejection (e.g. method not in
        // Access-Control-Allow-Methods). Both look identical from the
        // browser's perspective; the message hints at both so operators
        // don't waste time restarting a healthy server.
        throw new Error(
          `Backend unreachable at ${this.baseUrl}. Either the server is down ` +
          `or the request was blocked by CORS — check DevTools → Network ` +
          `for the preflight OPTIONS response.`
        );
      }
      throw error;
    }
  };

  post = async <T>(endpoint: string, data: any): Promise<T> => {
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        method: 'POST',
        headers: this.getAuthHeaders(),
        body: JSON.stringify(data),
      });

      if (response.status === 401) {
        sessionStorage.removeItem('auth_token');
        window.location.reload();
        throw new Error('Authentication expired. Please log in again.');
      }

      if (!response.ok) {
        throw new Error(`API request failed: ${response.status} ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        // Browser fetch threw TypeError — could be either the backend being
        // down OR a CORS preflight rejection (e.g. method not in
        // Access-Control-Allow-Methods). Both look identical from the
        // browser's perspective; the message hints at both so operators
        // don't waste time restarting a healthy server.
        throw new Error(
          `Backend unreachable at ${this.baseUrl}. Either the server is down ` +
          `or the request was blocked by CORS — check DevTools → Network ` +
          `for the preflight OPTIONS response.`
        );
      }
      throw error;
    }
  };

  put = async <T>(endpoint: string, data: any): Promise<T> => {
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        method: 'PUT',
        headers: this.getAuthHeaders(),
        body: JSON.stringify(data),
      });

      if (response.status === 401) {
        sessionStorage.removeItem('auth_token');
        window.location.reload();
        throw new Error('Authentication expired. Please log in again.');
      }

      if (!response.ok) {
        throw new Error(`API request failed: ${response.status} ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        // Browser fetch threw TypeError — could be either the backend being
        // down OR a CORS preflight rejection (e.g. method not in
        // Access-Control-Allow-Methods). Both look identical from the
        // browser's perspective; the message hints at both so operators
        // don't waste time restarting a healthy server.
        throw new Error(
          `Backend unreachable at ${this.baseUrl}. Either the server is down ` +
          `or the request was blocked by CORS — check DevTools → Network ` +
          `for the preflight OPTIONS response.`
        );
      }
      throw error;
    }
  };

  patch = async <T>(endpoint: string, data: any): Promise<T> => {
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        method: 'PATCH',
        headers: this.getAuthHeaders(),
        body: JSON.stringify(data),
      });

      if (response.status === 401) {
        sessionStorage.removeItem('auth_token');
        window.location.reload();
        throw new Error('Authentication expired. Please log in again.');
      }

      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try {
          const body = await response.json();
          if (body?.detail) detail = String(body.detail);
        } catch {
          /* non-JSON error body */
        }
        throw new Error(detail);
      }

      return await response.json();
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        // Browser fetch threw TypeError — could be either the backend being
        // down OR a CORS preflight rejection (e.g. method not in
        // Access-Control-Allow-Methods). Both look identical from the
        // browser's perspective; the message hints at both so operators
        // don't waste time restarting a healthy server.
        throw new Error(
          `Backend unreachable at ${this.baseUrl}. Either the server is down ` +
          `or the request was blocked by CORS — check DevTools → Network ` +
          `for the preflight OPTIONS response.`
        );
      }
      throw error;
    }
  };

  delete = async (endpoint: string): Promise<any> => {
    try {
      const response = await fetch(`${this.baseUrl}${endpoint}`, {
        method: 'DELETE',
        headers: this.getAuthHeaders(),
      });

      if (response.status === 401) {
        sessionStorage.removeItem('auth_token');
        window.location.reload();
        throw new Error('Authentication expired. Please log in again.');
      }

      if (!response.ok) {
        let errorMessage = `API request failed: ${response.status} ${response.statusText}`;
        try {
          const errorData = await response.json();
          if (errorData.detail) {
            errorMessage = errorData.detail;
          }
        } catch {
          // Ignore JSON parse errors
        }
        const error = new Error(errorMessage);
        (error as any).response = { data: { detail: errorMessage } };
        throw error;
      }

      return await response.json();
    } catch (error) {
      if (error instanceof TypeError && error.message.includes('fetch')) {
        // Browser fetch threw TypeError — could be either the backend being
        // down OR a CORS preflight rejection (e.g. method not in
        // Access-Control-Allow-Methods). Both look identical from the
        // browser's perspective; the message hints at both so operators
        // don't waste time restarting a healthy server.
        throw new Error(
          `Backend unreachable at ${this.baseUrl}. Either the server is down ` +
          `or the request was blocked by CORS — check DevTools → Network ` +
          `for the preflight OPTIONS response.`
        );
      }
      throw error;
    }
  };

  getProjects = async (params?: {
    page?: number;
    page_size?: number;
    q?: string;
    status?: string;
  }): Promise<ProjectListResponse> => {
    const sp = new URLSearchParams();
    if (params?.page != null) sp.set('page', String(params.page));
    if (params?.page_size != null) sp.set('page_size', String(params.page_size));
    if (params?.q) sp.set('q', params.q);
    if (params?.status) sp.set('status', params.status);
    const qs = sp.toString();
    return this.get<ProjectListResponse>(`/projects${qs ? `?${qs}` : ''}`);
  };

  getProjectStats = async (): Promise<ProjectDashboardStats> => {
    return this.get<ProjectDashboardStats>('/stats/projects');
  };

  getProject = async (projectId: string): Promise<Project> => {
    return this.get<Project>(`/projects/${projectId}`);
  };

  createProject = async (request: CreateProjectRequest): Promise<Project> => {
    return this.post<Project>('/projects', request);
  };

  updateProject = async (projectId: string, request: UpdateProjectRequest): Promise<Project> => {
    return this.put<Project>(`/projects/${projectId}`, request);
  };

  deleteProject = async (projectId: string): Promise<any> => {
    return this.delete(`/projects/${projectId}`);
  };

  pauseProject = async (projectId: string): Promise<{ message: string; project_id: string }> => {
    return this.post(`/projects/${projectId}/pause`, {});
  };

  resumeProject = async (projectId: string): Promise<{ message: string; project_id: string }> => {
    return this.post(`/projects/${projectId}/resume`, {});
  };

  cancelProject = async (projectId: string): Promise<{ message: string; project_id: string }> => {
    return this.post(`/projects/${projectId}/cancel`, {});
  };

  getProjectMessages = async (projectId: string): Promise<Message[]> => {
    return this.get<Message[]>(`/projects/${projectId}/messages`);
  };

  submitInputResponse = async (response: InputResponse): Promise<any> => {
    return this.post('/input-response', response);
  };

  healthCheck = async (): Promise<{ status: string; projects: number }> => {
    return this.get('/health');
  };

  // Admin methods
  getAdminUsers = async () => {
    return this.get<any[]>('/admin/users');
  };

  createAdminUser = async (data: { name: string; email: string; password: string; role: string }) => {
    return this.post<any>('/admin/users', data);
  };

  updateAdminUser = async (
    email: string,
    data: { name?: string; role?: string; is_active?: number },
  ) => {
    return this.patch<any>(`/admin/users/${encodeURIComponent(email)}`, data);
  };

  resetAdminUserPassword = async (email: string, new_password: string) => {
    return this.post<any>(
      `/admin/users/${encodeURIComponent(email)}/reset-password`,
      { new_password },
    );
  };

  deleteAdminUser = async (email: string) => {
    return this.delete(`/admin/users/${encodeURIComponent(email)}`);
  };

  checkIsAdmin = async (): Promise<{ is_admin: boolean }> => {
    return this.get('/auth/admin-check');
  };
}

export const apiClient = new ApiClient();
export const api = apiClient;
