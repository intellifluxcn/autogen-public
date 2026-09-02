// Card component displaying stage status, progress, and messages.

import React from 'react';
import { Clock, CheckCircle, XCircle, AlertCircle } from 'lucide-react';
import { StageCardProps, StageStatus } from '../types';
import { useTranslation } from '../hooks/useTranslation';
import {
  cn,
  getStageStatusColor,
  getStageColor,
  getProgressBarColor,
  formatLocaleDateTime,
  formatDuration,
  formatProgress,
  getStageName,
  getStageDescription,
} from '../utils';

export const StageCard: React.FC<StageCardProps> = ({ project, stage, className }) => {
  const { t } = useTranslation();

  if (!project.stages || typeof project.stages !== 'object') {
    console.warn('Project stages is missing or invalid:', project);
    return null;
  }

  const stageInfo = project.stages[stage];

  if (!stageInfo) {
    console.warn(`Stage info not found for stage: ${stage}`, { availableStages: Object.keys(project.stages) });
    return null;
  }

  const getStatusIcon = () => {
    switch (stageInfo.status || StageStatus.PENDING) {
      case StageStatus.PENDING:
        return <Clock className="w-4 h-4 text-gray-400" />;
      case StageStatus.IN_PROGRESS:
        return <AlertCircle className="w-4 h-4 text-blue-500 animate-spin" />;
      case StageStatus.COMPLETED:
        return <CheckCircle className="w-4 h-4 text-green-500" />;
      case StageStatus.ERROR:
        return <XCircle className="w-4 h-4 text-red-500" />;
      default:
        return <Clock className="w-4 h-4 text-gray-400" />;
    }
  };

  const getCardClassName = () => {
    const baseClasses = "stage-card";
    const statusClasses = {
      [StageStatus.PENDING]: "pending",
      [StageStatus.IN_PROGRESS]: "in-progress",
      [StageStatus.COMPLETED]: "completed",
      [StageStatus.ERROR]: "error",
      [StageStatus.CANCELLED]: "pending",
      [StageStatus.PAUSED]: "paused",
    };

    return cn(baseClasses, statusClasses[stageInfo.status || StageStatus.PENDING], className);
  };

  const isActive = (stageInfo.status || StageStatus.PENDING) === StageStatus.IN_PROGRESS;
  const stageDuration = stageInfo.start_time
    ? formatDuration(
        stageInfo.start_time,
        stageInfo.end_time,
        stageInfo.status || StageStatus.PENDING,
      )
    : t('time.placeholder.dash');

  return (
    <div className={getCardClassName()}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex min-w-0 items-center space-x-2">
          {getStatusIcon()}
          <h3 className={cn("truncate whitespace-nowrap font-semibold text-sm", getStageColor(stage))}>
            {getStageName(stage)}
          </h3>
          <span
            className="inline-flex h-5 min-w-5 flex-shrink-0 items-center justify-center rounded-full bg-gray-100 px-1.5 text-[11px] font-semibold text-gray-700"
            title={t('stage.artifacts')}
          >
            {stageInfo.artifact_count ?? 0}
          </span>
        </div>

        <div className="flex items-center space-x-2">
          <span className={cn("text-xs px-2 py-1 rounded-full", getStageStatusColor(stageInfo.status || StageStatus.PENDING))}>
            {t(`stage.${stageInfo.status || 'pending'}`)}
          </span>

        </div>
      </div>

      <div className="mb-3">
        <div className="flex justify-between items-center mb-1">
          <span className="text-xs text-gray-600">{getStageDescription(stage)}</span>
          <span className="text-xs font-medium text-gray-700">
            {formatProgress(stageInfo.progress)}
          </span>
        </div>

        <div className="progress-bar">
          <div
            className={cn("progress-fill", getProgressBarColor(stage))}
            style={{ width: `${stageInfo.progress * 100}%` }}
          />
        </div>
      </div>

      {(stageInfo.start_time || stageInfo.end_time) && (
        <div className="text-xs text-gray-500 mb-2">
          {stageInfo.start_time && (
            <div>{t('stage.start_time')}: {formatLocaleDateTime(stageInfo.start_time)}</div>
          )}
          {stageInfo.end_time && (
            <div>{t('stage.end_time')}: {formatLocaleDateTime(stageInfo.end_time)}</div>
          )}
          {stageInfo.start_time && (
            <div>
              {t('stage.duration')}: {stageDuration}
            </div>
          )}
        </div>
      )}

      {(stageInfo.status || StageStatus.PENDING) === StageStatus.ERROR && stageInfo.error_message && (
        <div className="text-xs text-red-600 bg-red-50 p-2 rounded mb-2">
          {stageInfo.error_message}
        </div>
      )}

      {isActive && (
        <div className="flex items-center space-x-2 text-xs text-blue-600 mb-2">
          <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
          <span>{t('stage.active')}</span>
        </div>
      )}

      {/* Expanded-messages section removed:
          1. Caused a "chevron flicker" UX bug when analyze messages
             briefly landed in the find card before backend refetch
             corrected the routing (see commit message for the parallel
             snapshot.current_stage fix in websocket_handler).
          2. The full message log is already available on the project
             detail page → Messages tab — duplicating a sliding window
             of 10 messages per card on the home list added noise without
             adding signal. The home cards now show only status,
             progress, timing and error_message. */}

      {stageInfo.output_data && (stageInfo.status || StageStatus.PENDING) === StageStatus.COMPLETED && (
        <div className="mt-3 pt-3 border-t border-gray-200">
          <div className="text-xs text-gray-600">
            <div className="font-medium mb-1">{t('stage.output')}:</div>
            <div className="bg-gray-50 p-2 rounded text-xs font-mono max-h-20 overflow-y-auto custom-scrollbar">
              {typeof stageInfo.output_data === 'string'
                ? stageInfo.output_data
                : JSON.stringify(stageInfo.output_data, null, 2)
              }
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
