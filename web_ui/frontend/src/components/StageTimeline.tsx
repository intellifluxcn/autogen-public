// Visual timeline of pipeline stages (find, analyze, download, qualify) with status indicators.

import { CheckCircle, Clock, XCircle, Search, FileText, Download, ShieldCheck } from 'lucide-react';
import type { DbStage } from '../types/database';
import { useTranslation } from '../hooks/useTranslation';
import { cn, formatLocaleDateTime } from '../utils';

interface StageTimelineProps {
  stages: DbStage[];
}

export function StageTimeline({ stages }: StageTimelineProps) {
  const { t } = useTranslation();
  const stageOrder = ['find', 'analyze', 'download', 'qualify'];
  const stageMap = new Map(stages.map(s => [s.stage_name, s]));

  const formatDuration = (seconds?: number) => {
    if (!seconds) return 'N/A';
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs}s`;
  };

  const getStageIcon = (stageName: string) => {
    switch (stageName) {
      case 'find':
        return Search;
      case 'analyze':
        return FileText;
      case 'download':
        return Download;
      case 'qualify':
        return ShieldCheck;
      default:
        return Clock;
    }
  };

  const getStatusDisplay = (stage?: DbStage) => {
    if (!stage) {
      return {
        icon: Clock,
        color: 'text-gray-400',
        bgColor: 'bg-gray-100',
        borderColor: 'border-gray-300',
      };
    }

    switch (stage.status) {
      case 'completed':
        return {
          icon: CheckCircle,
          color: 'text-green-600',
          bgColor: 'bg-green-100',
          borderColor: 'border-green-300',
        };
      case 'running':
        return {
          icon: Clock,
          color: 'text-yellow-600',
          bgColor: 'bg-yellow-100',
          borderColor: 'border-yellow-300',
        };
      case 'failed':
        return {
          icon: XCircle,
          color: 'text-red-600',
          bgColor: 'bg-red-100',
          borderColor: 'border-red-300',
        };
      default:
        return {
          icon: Clock,
          color: 'text-gray-400',
          bgColor: 'bg-gray-100',
          borderColor: 'border-gray-300',
        };
    }
  };

  return (
    <div className="space-y-4">
      {stageOrder.map((stageName, index) => {
        const stage = stageMap.get(stageName as 'find' | 'analyze' | 'download' | 'qualify');
        const statusDisplay = getStatusDisplay(stage);
        const StageIcon = getStageIcon(stageName);
        const StatusIcon = statusDisplay.icon;
        const isLast = index === stageOrder.length - 1;

        return (
          <div key={stageName} className="relative">
            {!isLast && (
              <div className="absolute left-6 top-12 bottom-0 w-0.5 bg-gray-200" style={{ height: '2rem' }} />
            )}

            <div className="flex items-start space-x-4">
              <div className={cn(
                "relative z-10 flex items-center justify-center w-12 h-12 rounded-full border-2",
                statusDisplay.bgColor,
                statusDisplay.borderColor
              )}>
                <StageIcon className={cn("w-6 h-6", statusDisplay.color)} />
              </div>

              <div className="flex-1 pt-1">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center space-x-2">
                    <h4 className="font-semibold text-gray-900">
                      {t(`pipeline.${stageName}`)}
                    </h4>
                    <StatusIcon className={cn("w-4 h-4", statusDisplay.color)} />
                  </div>
                  {stage?.duration_seconds !== undefined && (
                    <span className="text-sm text-gray-600">
                      {formatDuration(stage.duration_seconds)}
                    </span>
                  )}
                </div>

                {stage && (
                  <div className="text-sm text-gray-600 space-y-1">
                    {stage.start_time && (
                      <div>
                        {t('stage.start_time')}: {formatLocaleDateTime(stage.start_time)}
                      </div>
                    )}
                    {stage.end_time && (
                      <div>
                        {t('stage.end_time')}: {formatLocaleDateTime(stage.end_time)}
                      </div>
                    )}
                    {stage.error_message && (
                      <div className="text-red-600 mt-1">
                        {t('stage.error')}: {stage.error_message}
                      </div>
                    )}
                  </div>
                )}

                {!stage && (
                  <div className="text-sm text-gray-500">
                    {t('stage.not_started')}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
