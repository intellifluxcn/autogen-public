// Filterable execution log displaying project messages grouped by stage.

import { useState } from 'react';
import { Info, AlertTriangle, XCircle } from 'lucide-react';
import type { DbMessage } from '../types/database';
import { cn, formatLocaleDateTime } from '../utils';
import { useTranslation } from '../hooks/useTranslation';

interface MessageLogProps {
  messages: DbMessage[];
}

export function MessageLog({ messages }: MessageLogProps) {
  const { t } = useTranslation();
  const [selectedStage, setSelectedStage] = useState<string>('all');

  const stageOrder = ['find', 'analyze', 'download', 'qualify', 'pipeline'];
  // stage_name can be null for legacy/system messages — drop those so the
  // filter dropdown and formatStageLabel never receive null.
  const stageSet = new Set(
    messages
      .map((m) => m.stage_name)
      .filter((s): s is string => typeof s === 'string' && s.length > 0)
  );
  const orderedStages = stageOrder.filter((stage) => stageSet.has(stage));
  const extraStages = Array.from(stageSet).filter((stage) => !stageOrder.includes(stage));
  const stages = ['all', ...orderedStages, ...extraStages];

  const filteredMessages = selectedStage === 'all'
    ? messages
    : messages.filter(m => m.stage_name === selectedStage);

  const sortedMessages = [...filteredMessages].sort((a, b) =>
    new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  const getMessageIcon = (type: string) => {
    switch (type) {
      case 'info':
      case 'system':
        return Info;
      case 'warning':
        return AlertTriangle;
      case 'error':
        return XCircle;
      default:
        return Info;
    }
  };

  const getMessageColor = (type: string) => {
    switch (type) {
      case 'info':
      case 'system':
        return {
          bg: 'bg-blue-50',
          border: 'border-blue-200',
          text: 'text-blue-800',
          icon: 'text-blue-600',
        };
      case 'warning':
        return {
          bg: 'bg-yellow-50',
          border: 'border-yellow-200',
          text: 'text-yellow-800',
          icon: 'text-yellow-600',
        };
      case 'error':
        return {
          bg: 'bg-red-50',
          border: 'border-red-200',
          text: 'text-red-800',
          icon: 'text-red-600',
        };
      default:
        return {
          bg: 'bg-gray-50',
          border: 'border-gray-200',
          text: 'text-gray-800',
          icon: 'text-gray-600',
        };
    }
  };

  const formatStageLabel = (stage: string) => {
    if (stage === 'all') return t('messages.stage.all');
    if (stage === 'pipeline') return t('detail.pipeline');
    if (stage === 'find' || stage === 'analyze' || stage === 'download' || stage === 'qualify') {
      return t(`stage.${stage}`);
    }
    return stage.charAt(0).toUpperCase() + stage.slice(1);
  };

  if (messages.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        {t('messages.no_messages')}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center space-x-2">
        <div className="flex space-x-2">
          {stages.map((stage) => (
            <button
              key={stage}
              onClick={() => setSelectedStage(stage)}
              className={cn(
                "px-3 py-1 rounded-full text-sm font-medium transition-colors",
                selectedStage === stage
                  ? "bg-blue-600 text-white"
                  : "bg-gray-100 text-gray-700 hover:bg-gray-200"
              )}
            >
              {formatStageLabel(stage)}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-2 max-h-96 overflow-y-auto">
        {sortedMessages.map((message) => {
          const Icon = getMessageIcon(message.message_type);
          const colors = getMessageColor(message.message_type);

          return (
            <div
              key={message.id}
              className={cn(
                "border rounded-lg p-3",
                colors.bg,
                colors.border
              )}
            >
              <div className="flex items-start space-x-3">
                <Icon className={cn("w-5 h-5 flex-shrink-0 mt-0.5", colors.icon)} />
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mb-1">
                    <span className={cn("text-xs font-medium", colors.text)}>
                      {(message.stage_name ?? '').toUpperCase()}
                    </span>
                    <span className="text-xs text-gray-500">•</span>
                    <span className="text-xs text-gray-600">
                      {message.team_name}
                    </span>
                    <span className="text-xs text-gray-500 whitespace-nowrap ml-auto">
                      {formatLocaleDateTime(message.timestamp)}
                    </span>
                  </div>
                  <p
                    className={cn(
                      "text-sm whitespace-pre-wrap break-words [overflow-wrap:anywhere]",
                      colors.text
                    )}
                  >
                    {message.content}
                  </p>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {sortedMessages.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          {t('messages.no_messages')}
        </div>
      )}
    </div>
  );
}
