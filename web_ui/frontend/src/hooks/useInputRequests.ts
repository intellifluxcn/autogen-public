// Lightweight HITL input hook without project list polling.

import { useCallback, useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { InputRequest, InputResponse } from '../types';
import { apiClient } from '../utils/api';
import { socketClient } from '../utils/socket';

interface UseInputRequestsOptions {
  projectId?: string;
}

export const useInputRequests = ({ projectId }: UseInputRequestsOptions = {}) => {
  const [currentInputRequest, setCurrentInputRequest] = useState<InputRequest | null>(null);

  const submitInputMutation = useMutation({
    mutationFn: (response: InputResponse) => apiClient.submitInputResponse(response),
    onSuccess: () => setCurrentInputRequest(null),
  });

  const handleInputRequested = useCallback((data: any) => {
    const requestProjectId = String(data.project_id || '');
    if (projectId && requestProjectId !== projectId) {
      return;
    }

    const inputRequest: InputRequest = {
      id: data.data.input_id,
      project_id: requestProjectId,
      prompt: data.data.prompt,
      team_name: data.data.team_name,
      timestamp: new Date().toISOString(),
    };
    setCurrentInputRequest(inputRequest);
  }, [projectId]);

  const handleInputResponse = useCallback((data: any) => {
    const requestProjectId = String(data.project_id || '');
    if (projectId && requestProjectId !== projectId) {
      return;
    }

    if (currentInputRequest?.id === data.data.input_id) {
      setCurrentInputRequest(null);
    }
  }, [currentInputRequest, projectId]);

  useEffect(() => {
    const socket = socketClient.connect();
    const handleConnect = () => {
      // no-op; ensure connection is initialized for input events
    };
    socket.on('connect', handleConnect);

    const unsubscribers = [
      socketClient.on('input_requested', handleInputRequested),
      socketClient.on('input_response', handleInputResponse),
    ];

    return () => {
      socket.off('connect', handleConnect);
      unsubscribers.forEach((unsubscribe) => unsubscribe());
    };
  }, [handleInputRequested, handleInputResponse]);

  const submitInputResponse = useCallback((response: string) => {
    if (!currentInputRequest) return Promise.reject('No input request');

    const inputResponse: InputResponse = {
      input_id: currentInputRequest.id,
      response,
    };

    return submitInputMutation.mutateAsync(inputResponse);
  }, [currentInputRequest, submitInputMutation]);

  const closeInputModal = useCallback(() => {
    setCurrentInputRequest(null);
  }, []);

  return {
    currentInputRequest,
    submitInputResponse,
    closeInputModal,
    isSubmittingInput: submitInputMutation.isPending,
    submitInputError: submitInputMutation.error,
  };
};
