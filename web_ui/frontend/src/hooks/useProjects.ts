// React hook for project CRUD, WebSocket event handling, and real-time state updates.

import { useState, useEffect, useCallback, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Project, ProjectListResponse, CreateProjectRequest, InputRequest, InputResponse, PipelineStage } from '../types';
import { apiClient } from '../utils/api';
import { socketClient } from '../utils/socket';
import { DEFAULT_PROJECTS_PAGE_SIZE } from '../config/projectsPageSize';

const PROJECTS_QK = 'projects';
const PROJECTS_STATS_QK = 'projects-stats';

// Coalesce WS bursts (e.g. project_updated flood after reconnect) into a
// single refetch but keep the window short enough to be imperceptible —
// the mutation success path uses optimistic setQueryData below, so this
// debounce no longer gates the "new project appears" UX.
const INVALIDATE_DEBOUNCE_MS = 50;

function projectsQueryKey(page: number, search: string, statusFilter: string) {
  return [PROJECTS_QK, page, DEFAULT_PROJECTS_PAGE_SIZE, search, statusFilter] as const;
}

export const useProjects = () => {
  const queryClient = useQueryClient();
  const invalidateTimerRef = useRef<number | null>(null);
  const [currentInputRequest, setCurrentInputRequest] = useState<InputRequest | null>(null);
  const [isSocketConnected, setIsSocketConnected] = useState(false);
  const [currentThinking, setCurrentThinking] = useState<{ thinking: string; agentName: string | null } | null>(null);
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedSearch(searchInput.trim()), 300);
    return () => window.clearTimeout(id);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, statusFilter]);

  const invalidateProjectQueries = useCallback(() => {
    if (invalidateTimerRef.current !== null) {
      window.clearTimeout(invalidateTimerRef.current);
    }
    invalidateTimerRef.current = window.setTimeout(() => {
      queryClient.invalidateQueries({ queryKey: [PROJECTS_QK] });
      queryClient.invalidateQueries({ queryKey: [PROJECTS_STATS_QK] });
      invalidateTimerRef.current = null;
    }, INVALIDATE_DEBOUNCE_MS);
  }, [queryClient]);

  useEffect(() => {
    return () => {
      if (invalidateTimerRef.current !== null) {
        window.clearTimeout(invalidateTimerRef.current);
      }
    };
  }, []);

  const {
    data: projectsPage,
    isLoading,
    isFetching,
    error,
    refetch: refetchProjects,
  } = useQuery({
    queryKey: projectsQueryKey(page, debouncedSearch, statusFilter),
    queryFn: () =>
      apiClient.getProjects({
        page,
        page_size: DEFAULT_PROJECTS_PAGE_SIZE,
        q: debouncedSearch || undefined,
        status: statusFilter || undefined,
      }),
    placeholderData: (previousData) => previousData,
    refetchInterval: 30000,
    retry: 3,
    retryDelay: 1000,
  });

  const { data: projectStats, refetch: refetchStats } = useQuery({
    queryKey: [PROJECTS_STATS_QK],
    queryFn: () => apiClient.getProjectStats(),
    refetchInterval: 30000,
    retry: 3,
    retryDelay: 1000,
  });

  const projects = projectsPage?.items ?? [];
  const totalProjects = projectsPage?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalProjects / DEFAULT_PROJECTS_PAGE_SIZE));

  useEffect(() => {
    if (isFetching) return;
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages, isFetching]);

  const createProjectMutation = useMutation({
    mutationFn: (request: CreateProjectRequest) => apiClient.createProject(request),
    onSuccess: (newProject: Project) => {
      // Optimistically prepend the new project into every cached projects
      // page so it shows up before the refetch round-trip completes.
      // Backend sorts by updated_at DESC and inserts in 'running' status,
      // so the new row belongs at the head of page-1 caches whose filter
      // matches (no status filter or status='running'; search term
      // matches the name/query).
      const entries = queryClient.getQueriesData<ProjectListResponse>({
        queryKey: [PROJECTS_QK],
      });
      for (const [key, old] of entries) {
        if (!old) continue;
        // key shape: [PROJECTS_QK, page, pageSize, search, statusFilter]
        const pageNum = (key as readonly unknown[])[1] as number | undefined;
        const search = ((key as readonly unknown[])[3] as string | undefined) || '';
        const status = ((key as readonly unknown[])[4] as string | undefined) || '';

        if (status && newProject.status !== status) continue;
        if (search.trim()) {
          const term = search.trim().toLowerCase();
          const matches =
            newProject.name?.toLowerCase().includes(term) ||
            newProject.query?.toLowerCase().includes(term);
          if (!matches) continue;
        }
        if (old.items.some((p) => p.id === newProject.id)) continue;

        if (pageNum !== 1) {
          // New row will land on page 1 server-side; downstream pages only
          // need the total bumped so pagination counters stay correct.
          queryClient.setQueryData<ProjectListResponse>(key, {
            ...old,
            total: old.total + 1,
          });
          continue;
        }
        const truncated = old.items.slice(0, Math.max(0, old.page_size - 1));
        queryClient.setQueryData<ProjectListResponse>(key, {
          ...old,
          items: [newProject, ...truncated],
          total: old.total + 1,
        });
      }
      // Bump the running counter optimistically — backend inserts new
      // projects with status='running'.
      queryClient.setQueryData<Record<string, number>>(
        [PROJECTS_STATS_QK],
        (old) => (old ? { ...old, running: (old.running ?? 0) + 1 } : old)
      );
      // Schedule background reconciliation so any drift gets corrected.
      invalidateProjectQueries();
    },
  });

  const submitInputMutation = useMutation({
    mutationFn: (response: InputResponse) => apiClient.submitInputResponse(response),
    onSuccess: () => setCurrentInputRequest(null),
  });

  const handleProjectCreated = useCallback(() => {
    invalidateProjectQueries();
  }, [invalidateProjectQueries]);

  const handleProjectUpdated = useCallback(() => {
    invalidateProjectQueries();
  }, [invalidateProjectQueries]);

  const handleProjectDeleted = useCallback(() => {
    invalidateProjectQueries();
  }, [invalidateProjectQueries]);

  const handleStageUpdated = useCallback(
    (data: any) => {
      queryClient.setQueryData(
        projectsQueryKey(page, debouncedSearch, statusFilter),
        (old: { items: Project[]; total: number; page: number; page_size: number } | undefined) => {
          if (!old?.items || !data?.project_id) return old;
          const stage = data.data?.stage as PipelineStage;
          const items = old.items.map((project) => {
            if (project.id !== data.project_id) return project;
            const updated = { ...project };
            if (stage && updated.stages[stage]) {
              updated.stages[stage] = {
                ...updated.stages[stage],
                status: data.data.status,
                progress: data.data.progress ?? updated.stages[stage].progress,
              };
              updated.overall_progress = data.data.overall_progress ?? updated.overall_progress;
            }
            return updated;
          });
          return { ...old, items };
        }
      );
    },
    [queryClient, page, debouncedSearch, statusFilter]
  );

  const handleProgressUpdated = useCallback(
    (data: any) => {
      queryClient.setQueryData(
        projectsQueryKey(page, debouncedSearch, statusFilter),
        (old: { items: Project[]; total: number; page: number; page_size: number } | undefined) => {
          if (!old?.items || !data?.project_id) return old;
          const stage = data.data?.stage as PipelineStage;
          const items = old.items.map((project) => {
            if (project.id !== data.project_id) return project;
            const updated = { ...project };
            if (stage && updated.stages[stage]) {
              updated.stages[stage].progress = data.data.progress;
              updated.overall_progress = data.data.overall_progress ?? updated.overall_progress;
            }
            return updated;
          });
          return { ...old, items };
        }
      );
    },
    [queryClient, page, debouncedSearch, statusFilter]
  );

  const handleMessageAdded = useCallback(
    (data: any) => {
      queryClient.setQueryData(
        projectsQueryKey(page, debouncedSearch, statusFilter),
        (old: { items: Project[]; total: number; page: number; page_size: number } | undefined) => {
          if (!old?.items || !data?.project_id) return old;
          const stage = data.data?.stage as PipelineStage;
          const items = old.items.map((project) => {
            if (project.id !== data.project_id) return project;
            const updated = { ...project };
            if (stage && updated.stages[stage]) {
              updated.stages[stage].messages = [
                ...updated.stages[stage].messages,
                data.data.message,
              ];
            }
            return updated;
          });
          return { ...old, items };
        }
      );
    },
    [queryClient, page, debouncedSearch, statusFilter]
  );

  const handleInputRequested = useCallback((data: any) => {
    const inputRequest: InputRequest = {
      id: data.data.input_id,
      project_id: data.project_id!,
      prompt: data.data.prompt,
      team_name: data.data.team_name,
      timestamp: new Date().toISOString(),
    };
    setCurrentInputRequest(inputRequest);
  }, []);

  const handleInputResponse = useCallback((data: any) => {
    if (currentInputRequest?.id === data.data.input_id) {
      setCurrentInputRequest(null);
    }
  }, [currentInputRequest]);

  const handleThinkingUpdated = useCallback((data: any) => {
    setCurrentThinking({
      thinking: data.data.thinking,
      agentName: data.data.agent_name || null
    });
  }, []);

  useEffect(() => {
    const socket = socketClient.connect();
    const handleConnect = () => setIsSocketConnected(true);
    const handleDisconnect = () => setIsSocketConnected(false);
    socket.on('connect', handleConnect);
    socket.on('disconnect', handleDisconnect);
    setIsSocketConnected(socket.connected);

    const unsubscribers = [
      socketClient.on('project_created', handleProjectCreated),
      socketClient.on('project_updated', handleProjectUpdated),
      socketClient.on('project_deleted', handleProjectDeleted),
      socketClient.on('stage_updated', handleStageUpdated),
      socketClient.on('progress_updated', handleProgressUpdated),
      socketClient.on('message_added', handleMessageAdded),
      socketClient.on('input_requested', handleInputRequested),
      socketClient.on('input_response', handleInputResponse),
      socketClient.on('thinking_updated', handleThinkingUpdated),
    ];

    return () => {
      socket.off('connect', handleConnect);
      socket.off('disconnect', handleDisconnect);
      unsubscribers.forEach(unsubscribe => unsubscribe());
    };
  }, [
    handleProjectCreated,
    handleProjectUpdated,
    handleProjectDeleted,
    handleStageUpdated,
    handleProgressUpdated,
    handleMessageAdded,
    handleInputRequested,
    handleInputResponse,
    handleThinkingUpdated,
  ]);

  const createProject = useCallback((request: CreateProjectRequest) => {
    return createProjectMutation.mutateAsync(request);
  }, [createProjectMutation]);

  const submitInputResponse = useCallback((response: string) => {
    if (!currentInputRequest) return Promise.reject('No input request');

    const inputResponse: InputResponse = {
      input_id: currentInputRequest.id,
      response,
    };

    return submitInputMutation.mutateAsync(inputResponse);
  }, [currentInputRequest, submitInputMutation]);

  const joinProject = useCallback((projectId: string) => {
    socketClient.joinProject(projectId);
  }, []);

  const closeInputModal = useCallback(() => {
    setCurrentInputRequest(null);
  }, []);

  const pauseProject = useCallback(async (projectId: string) => {
    try {
      await apiClient.pauseProject(projectId);
      invalidateProjectQueries();
    } catch (error) {
      invalidateProjectQueries();
      throw error;
    }
  }, [invalidateProjectQueries]);

  const resumeProject = useCallback(async (projectId: string) => {
    try {
      await apiClient.resumeProject(projectId);
      invalidateProjectQueries();
    } catch (error) {
      invalidateProjectQueries();
      throw error;
    }
  }, [invalidateProjectQueries]);

  const cancelProject = useCallback(async (projectId: string) => {
    try {
      await apiClient.cancelProject(projectId);
      invalidateProjectQueries();
    } catch (error) {
      invalidateProjectQueries();
      throw error;
    }
  }, [invalidateProjectQueries]);

  const refetch = useCallback(async () => {
    await Promise.all([refetchProjects(), refetchStats()]);
  }, [refetchProjects, refetchStats]);

  const stats = projectStats ?? {
    pending: 0,
    running: 0,
    paused: 0,
    completed: 0,
    failed: 0,
    active: 0,
    in_progress: 0,
  };

  return {
    projects,
    totalProjects,
    page,
    setPage,
    totalPages,
    pageSize: DEFAULT_PROJECTS_PAGE_SIZE,
    searchInput,
    setSearchInput,
    statusFilter,
    setStatusFilter,
    projectStats: stats,
    currentInputRequest,
    currentThinking,
    isLoading,
    isFetchingProjects: isFetching,
    isCreatingProject: createProjectMutation.isPending,
    isSubmittingInput: submitInputMutation.isPending,
    isSocketConnected,
    error,
    createProjectError: createProjectMutation.error,
    submitInputError: submitInputMutation.error,
    createProject,
    submitInputResponse,
    joinProject,
    closeInputModal,
    refetch,
    pauseProject,
    resumeProject,
    cancelProject,
  };
};
