// Displays projects as rows in a grid with stage cards and overall progress.

import React, { useState } from 'react';
import { Search, Pause, Play, X, ChevronRight } from 'lucide-react';
import { ProjectGridProps, PipelineStage, StageStatus } from '../types';
import { StageCard } from './StageCard';
import {
  cn,
  formatTimestamp,
  getStageStatusColor
} from '../utils';
import { useTranslation } from '../hooks/useTranslation';

export const ProjectGrid: React.FC<ProjectGridProps> = ({
  projects,
  onSelectProject,
  onPauseProject,
  onResumeProject,
  onCancelProject
}) => {
  const { t } = useTranslation();
  const [actioningProject, setActioningProject] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<'pause' | 'resume' | 'delete' | null>(null);
  const [confirmProjectId, setConfirmProjectId] = useState<string | null>(null);
  const [confirmProjectName, setConfirmProjectName] = useState<string>('');
  const stages = [PipelineStage.FIND, PipelineStage.ANALYZE, PipelineStage.DOWNLOAD, PipelineStage.QUALIFY];

  const resetConfirmDialog = () => {
    setConfirmAction(null);
    setConfirmProjectId(null);
    setConfirmProjectName('');
  };

  const openConfirmDialog = (action: 'pause' | 'resume' | 'delete', projectId: string, projectName: string) => {
    setConfirmAction(action);
    setConfirmProjectId(projectId);
    setConfirmProjectName(projectName);
  };

  const handleConfirmAction = async () => {
    if (!confirmAction || !confirmProjectId) {
      return;
    }

    const actionHandler = {
      pause: onPauseProject,
      resume: onResumeProject,
      delete: onCancelProject,
    }[confirmAction];

    if (!actionHandler) {
      return;
    }

    setActioningProject(confirmProjectId);
    try {
      await actionHandler(confirmProjectId);
      resetConfirmDialog();
    } catch (error) {
      console.error(`Failed to ${confirmAction} project:`, error);
    } finally {
      setActioningProject(null);
    }
  };

  const confirmTitleKey = confirmAction ? `grid.${confirmAction}_title` : '';
  const confirmTextKey = confirmAction ? `grid.${confirmAction}_confirm` : '';
  const confirmButtonTextKey =
    confirmAction === 'delete' ? 'common.delete' : confirmAction === 'pause' ? 'grid.pause' : 'grid.resume';
  const confirmMessage =
    confirmAction === 'delete'
      ? `${t('grid.delete_confirm')}${confirmProjectName}${t('grid.delete_confirm_suffix')}`
      : `${t(confirmTextKey)} "${confirmProjectName}"?`;
  const confirmButtonClassName = cn(
    'px-3 py-2 text-sm rounded text-white disabled:opacity-50',
    confirmAction === 'delete'
      ? 'bg-red-600 hover:bg-red-700'
      : confirmAction === 'pause'
        ? 'bg-yellow-600 hover:bg-yellow-700'
        : 'bg-green-600 hover:bg-green-700'
  );

  if (projects.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        <div className="text-center">
          <Search className="w-12 h-12 mx-auto mb-4 opacity-50" />
          <p className="text-lg font-medium">{t('grid.no_projects')}</p>
          <p className="text-sm">{t('grid.create_first')}</p>
        </div>
      </div>
    );
  }

  return (
    <>
    <div className="bg-white rounded-lg shadow-sm border">
      {/* Header row — only shown on lg+ where the table layout fits */}
      <div className="hidden lg:grid grid-cols-[300px_repeat(4,1fr)] gap-4 p-4 bg-gray-50 border-b">
        <div className="font-semibold text-gray-700 whitespace-nowrap">{t('grid.project')}</div>
        <div className="font-semibold text-gray-700 text-center whitespace-nowrap">{t('grid.find_papers')}</div>
        <div className="font-semibold text-gray-700 text-center whitespace-nowrap">{t('grid.analyze')}</div>
        <div className="font-semibold text-gray-700 text-center whitespace-nowrap">{t('grid.download')}</div>
        <div className="font-semibold text-gray-700 text-center whitespace-nowrap">{t('grid.qualify')}</div>
      </div>

      <div className="divide-y divide-gray-100">
        {projects.filter(p => p != null).map((project) => {
          return (
          <div
            key={project.id}
            className="flex flex-col gap-4 p-4 lg:grid lg:grid-cols-[300px_repeat(4,1fr)] lg:gap-4 transition-colors"
          >
            <div
              role="button"
              tabIndex={0}
              onClick={() => onSelectProject?.(project)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onSelectProject?.(project);
                }
              }}
              className="group cursor-pointer rounded-lg -m-2 p-2 transition-all duration-150 outline-none [-webkit-tap-highlight-color:transparent] hover:bg-gray-50 hover:shadow-sm focus:outline-none focus-visible:bg-gray-50 focus-visible:shadow-sm"
            >
              <div className="space-y-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3
                      className="font-medium text-blue-700 group-hover:text-blue-800 truncate transition-colors"
                      title={project.name}
                    >
                      {project.name}
                    </h3>
                    <p className="line-clamp-2 break-all text-sm text-gray-600" title={project.query}>
                      {project.query}
                    </p>
                  </div>
                  <ChevronRight
                    className="w-4 h-4 text-gray-300 group-hover:text-blue-500 group-hover:translate-x-0.5 shrink-0 mt-1 transition-all"
                    aria-hidden
                  />
                </div>

                <div className="text-xs text-gray-500">
                  {formatTimestamp(project.created_at)}
                </div>

                <div className="flex items-center">
                  <span className={cn(
                    "text-xs px-2 py-1 rounded-full whitespace-nowrap",
                    getStageStatusColor(project.status || StageStatus.PENDING)
                  )}>
                    {t(`stage.${project.status || 'pending'}`)}
                  </span>
                </div>

                <div className="flex items-center space-x-1 mt-2">
                  {project.status === StageStatus.IN_PROGRESS && onPauseProject && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        openConfirmDialog('pause', project.id, project.name);
                      }}
                      disabled={actioningProject === project.id}
                      className={cn(
                        "flex items-center space-x-1 px-2 py-1 text-xs rounded",
                        "bg-yellow-100 text-yellow-700 hover:bg-yellow-200",
                        "transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      )}
                      title={t('grid.pause_title')}
                    >
                      <Pause className="w-3 h-3" />
                      <span>{t('grid.pause')}</span>
                    </button>
                  )}

                  {(project.status === StageStatus.PAUSED || project.status === StageStatus.ERROR) && onResumeProject && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        openConfirmDialog('resume', project.id, project.name);
                      }}
                      disabled={actioningProject === project.id}
                      className={cn(
                        "flex items-center space-x-1 px-2 py-1 text-xs rounded",
                        "bg-green-100 text-green-700 hover:bg-green-200",
                        "transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      )}
                      title={t('grid.resume_title')}
                    >
                      <Play className="w-3 h-3" />
                      <span>{t('grid.resume')}</span>
                    </button>
                  )}

                  {(project.status === StageStatus.IN_PROGRESS ||
                    project.status === StageStatus.PAUSED ||
                    project.status === StageStatus.ERROR ||
                    project.status === StageStatus.COMPLETED) && onCancelProject && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        openConfirmDialog('delete', project.id, project.name);
                      }}
                      disabled={actioningProject === project.id}
                      className={cn(
                        "flex items-center space-x-1 px-2 py-1 text-xs rounded",
                        "bg-red-100 text-red-700 hover:bg-red-200",
                        "transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      )}
                      title={t('grid.delete_title')}
                    >
                      <X className="w-3 h-3" />
                      <span>{t('grid.delete')}</span>
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Stage cards — sub-grid below lg so they sit side-by-side instead
                of stacking to full width; `lg:contents` makes the wrapper
                disappear from layout on desktop so cards fill the parent grid */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 lg:contents">
              {stages.map((stage) => (
                <div key={stage} className="min-h-[120px]">
                  {project.stages && typeof project.stages === 'object' ? (
                    <StageCard
                      project={project}
                      stage={stage}
                      className="h-full"
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-gray-400 text-sm">
                      {t('grid.loading')}
                    </div>
                  )}
                </div>
              ))}
            </div>

          </div>
          );
        })}
      </div>
    </div>
    {confirmAction && confirmProjectId && (
      <div
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
        onClick={() => {
          if (actioningProject !== confirmProjectId) {
            resetConfirmDialog();
          }
        }}
      >
        <div
          className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
          onClick={(e) => e.stopPropagation()}
        >
          <h3 className="text-lg font-semibold text-gray-900">{t(confirmTitleKey)}</h3>
          <p className="mt-2 text-sm text-gray-700">{confirmMessage}</p>
          <div className="mt-5 flex justify-end space-x-2">
            <button
              onClick={resetConfirmDialog}
              disabled={actioningProject === confirmProjectId}
              className="px-3 py-2 text-sm rounded border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={handleConfirmAction}
              disabled={actioningProject === confirmProjectId}
              className={confirmButtonClassName}
            >
              {t(confirmButtonTextKey)}
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
};
