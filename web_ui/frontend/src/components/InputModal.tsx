// Modal for human-in-the-loop input requests from pipeline agents.

import React, { useState, useEffect } from 'react';
import { X, Send, AlertCircle } from 'lucide-react';
import { InputModalProps } from '../types';
import { cn, formatTimestamp } from '../utils';
import { useTranslation } from '../hooks/useTranslation';

export const InputModal: React.FC<InputModalProps> = ({
  isOpen,
  inputRequest,
  onSubmit,
  onClose
}) => {
  const { t } = useTranslation();
  const [response, setResponse] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (isOpen && inputRequest) {
      setResponse('');
    }
  }, [isOpen, inputRequest?.id]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    const trimmedResponse = response.trim();
    if (!trimmedResponse || isSubmitting) return;

    setIsSubmitting(true);
    try {
      await onSubmit(trimmedResponse);
      setResponse('');
    } catch (error) {
      console.error('Failed to submit response:', error);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      handleSubmit(e);
    }
  };

  if (!isOpen || !inputRequest) {
    return null;
  }

  return (
    <>
      <div
        className="fixed inset-0 bg-black bg-opacity-50 z-40 transition-opacity"
        onClick={onClose}
      />

      <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div
          className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between p-6 border-b">
            <div className="flex items-center space-x-3">
              <AlertCircle className="w-5 h-5 text-blue-500" />
              <div>
                <h2 className="text-lg font-semibold text-gray-900">
                  {t('input.title')}
                </h2>
                {inputRequest.team_name && (
                  <p className="text-sm text-gray-600">
                    {t('input.from')}: {inputRequest.team_name}
                  </p>
                )}
              </div>
            </div>

            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 transition-colors"
              disabled={isSubmitting}
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="flex-1 p-6 overflow-y-auto">
            <div className="space-y-4">
              <div className="text-sm text-gray-500">
                {t('input.requested')}: {formatTimestamp(inputRequest.timestamp)}
              </div>

              <div className="bg-blue-50 p-4 rounded-lg border border-blue-200">
                <p className="text-gray-800 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                  {inputRequest.prompt}
                </p>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label
                    htmlFor="response-input"
                    className="block text-sm font-medium text-gray-700 mb-2"
                  >
                    {t('input.response_label')}
                  </label>
                  <textarea
                    id="response-input"
                    value={response}
                    onChange={(e) => setResponse(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={t('input.placeholder')}
                    className={cn(
                      "w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm",
                      "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                      "resize-none transition-colors",
                      isSubmitting && "opacity-50 cursor-not-allowed"
                    )}
                    rows={4}
                    disabled={isSubmitting}
                    autoFocus
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    {t('input.hint')}
                  </p>
                </div>

                <div className="flex justify-end space-x-3 pt-4 border-t">
                  <button
                    type="button"
                    onClick={onClose}
                    disabled={isSubmitting}
                    className={cn(
                      "px-4 py-2 text-sm font-medium text-gray-700 bg-white",
                      "border border-gray-300 rounded-md shadow-sm",
                      "hover:bg-gray-50 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                      "transition-colors",
                      isSubmitting && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    {t('input.cancel')}
                  </button>

                  <button
                    type="submit"
                    disabled={!response.trim() || isSubmitting}
                    className={cn(
                      "px-4 py-2 text-sm font-medium text-white",
                      "bg-blue-600 border border-transparent rounded-md shadow-sm",
                      "hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2",
                      "transition-colors flex items-center space-x-2",
                      (!response.trim() || isSubmitting) && "opacity-50 cursor-not-allowed"
                    )}
                  >
                    {isSubmitting ? (
                      <>
                        <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        <span>{t('input.submitting')}</span>
                      </>
                    ) : (
                      <>
                        <Send className="w-4 h-4" />
                        <span>{t('input.submit')}</span>
                      </>
                    )}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};
