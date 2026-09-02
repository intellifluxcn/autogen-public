// Project creation form and statistics dashboard.

import React, { useState } from 'react';
import { Plus, Search, RefreshCw, HelpCircle } from 'lucide-react';
import { ProjectListApiStatus, ProjectManagerProps } from '../types';

import { cn, isValidProjectName, isValidQuery } from '../utils';
import { useTranslation } from '../hooks/useTranslation';
import { useModels } from '../hooks/useModels';

export const ProjectManager: React.FC<ProjectManagerProps> = ({
  onCreateProject,
  isCreating = false,
  projectStats,
  search,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
  onRefresh,
  isRefreshing = false,
}) => {
  const { t } = useTranslation();
  // Note: `pending` is intentionally omitted — projects are inserted with
  // status='running' at creation, so the pending count is always 0 (see
  // database/dao.py:56 and websocket_handler.py:173). The enum is kept for
  // stage-level pending state but no project ever has it.
  const statusCards: Array<{
    status: ProjectListApiStatus;
    count: number;
    bgClass: string;
    selectedBgClass: string;
    selectedBorderClass: string;
    textClass: string;
  }> = [
    { status: 'running',   count: projectStats.running ?? 0,   bgClass: 'bg-amber-50',  selectedBgClass: 'bg-amber-100',  selectedBorderClass: 'border-amber-400',  textClass: 'text-amber-600' },
    { status: 'paused',    count: projectStats.paused ?? 0,    bgClass: 'bg-violet-50', selectedBgClass: 'bg-violet-100', selectedBorderClass: 'border-violet-400', textClass: 'text-violet-600' },
    { status: 'completed', count: projectStats.completed ?? 0, bgClass: 'bg-green-50',  selectedBgClass: 'bg-green-100',  selectedBorderClass: 'border-green-400',  textClass: 'text-green-600' },
    { status: 'failed',    count: projectStats.failed ?? 0,    bgClass: 'bg-red-50',    selectedBgClass: 'bg-red-100',    selectedBorderClass: 'border-red-400',    textClass: 'text-red-600' },
  ];
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [query, setQuery] = useState('');
  const [maxPapers, setMaxPapers] = useState(10);
  // Parallel-pipeline behaviour is now controlled by the backend's
  // PIPELINE_PARALLEL env var (single source of truth); no per-project UI.
  const [errors, setErrors] = useState<{ name?: string; query?: string; dateRange?: string }>({});
  const [analysisModel, setAnalysisModel] = useState('');
  const [downloadModel, setDownloadModel] = useState('');
  // date range filter (HTML5 native YYYY-MM-DD format).
  // Empty string = user didn't pick → backend uses Python-level defaults.
  const [dateStart, setDateStart] = useState('');
  const [dateEnd, setDateEnd] = useState('');
  // PubMed syntax help block visibility.
  const [showPubMedHelp, setShowPubMedHelp] = useState(false);
  // Advanced Options folding region (B4 escape hatch UI).
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [forceReanalyze, setForceReanalyze] = useState(false);
  // MeSH expansion toggle (default ON).
  const [meshExpansion, setMeshExpansion] = useState(true);

  // decision A2: detect users typing `[dp]` while ALSO using
  // the date picker. We warn (not block) so power users can still hand-craft
  // a date-range query — but they must clear the picker first.
  const hasInlineDateSyntax = /\[dp\]|\[pdat\]/i.test(query);
  const datePickerActive = Boolean(dateStart || dateEnd);
  const showDateConflict = hasInlineDateSyntax && datePickerActive;

  // /api/models is cached for 24h via useModels — opening the form once
  // hydrates the cache for both this form and the project detail page.
  const {
    data: modelsData,
    isPending: modelsPending,
    error: modelsErrorObj,
  } = useModels({ enabled: showCreateForm });
  const modelOptions = modelsData?.models ?? [];
  const modelDefaults = modelsData?.defaults ?? {};
  const modelsLoading = showCreateForm && modelsPending && !modelsData;
  const modelsError = modelsErrorObj ? modelsErrorObj.message : null;

  const validateForm = () => {
    const newErrors: { name?: string; query?: string; dateRange?: string } = {};

    if (!isValidProjectName(projectName)) {
      newErrors.name = t('error.project_name_invalid');
    }

    if (!isValidQuery(query)) {
      newErrors.query = t('error.query_invalid');
    }

    // when both date inputs are set, ensure start <= end.
    // Format from <input type="date"> is YYYY-MM-DD which is lexicographically
    // sortable, so a simple string compare is correct.
    if (dateStart && dateEnd && dateStart > dateEnd) {
      newErrors.dateRange = t('error.date_range_invalid');
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!validateForm()) return;

    // convert HTML5 date format (YYYY-MM-DD) to the backend's
    // expected YYYY/MM/DD before sending. Empty string → undefined so the
    // backend falls back to Python-level defaults.
    // CI-fix: use .replace(/-/g, '/') instead of .replaceAll (ES2021) to
    // avoid bumping the TypeScript lib target.
    const formatDate = (d: string) => (d ? d.replace(/-/g, '/') : undefined);

    onCreateProject({
      name: projectName.trim(),
      query: query.trim(),
      max_papers: maxPapers,
      analysis_model: analysisModel || undefined,
      download_model: downloadModel || undefined,
      date_start: formatDate(dateStart),
      date_end: formatDate(dateEnd),
      // only send when explicitly opted-in
      force_reanalyze: forceReanalyze || undefined,
      // only send when user changed from default (true)
      mesh_expansion: meshExpansion ? undefined : false,
    });

    setProjectName('');
    setQuery('');
    setMaxPapers(10);
    setAnalysisModel('');
    setDownloadModel('');
    setDateStart('');
    setDateEnd('');
    setShowPubMedHelp(false);
    setShowAdvanced(false);
    setForceReanalyze(false);
    setMeshExpansion(true);
    setShowCreateForm(false);
    setErrors({});
  };

  const handleCancel = () => {
    setShowCreateForm(false);
    setProjectName('');
    setQuery('');
    setMaxPapers(10);
    setAnalysisModel('');
    setDownloadModel('');
    setDateStart('');
    setDateEnd('');
    setShowPubMedHelp(false);
    setShowAdvanced(false);
    setForceReanalyze(false);
    setMeshExpansion(true);
    setErrors({});
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border">
      <div className="p-4 sm:p-6 border-b border-gray-200">
        {/* xs: title + 新项目 一行 → 搜索/刷新 一行 */}
        {/* sm+: 单行 [title | search refresh +新项目] */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center justify-between sm:block">
            <h1 className="text-2xl font-bold text-gray-900">
              {t('manager.title')}
            </h1>
            {/* xs 专用：新项目按钮紧贴标题右侧，主操作高可达性 */}
            <button
              type="button"
              onClick={() => setShowCreateForm(true)}
              disabled={isCreating}
              className={cn(
                "sm:hidden flex items-center gap-1.5 px-3 py-2 bg-blue-600 text-white rounded-md",
                "hover:bg-blue-700 transition-colors text-sm font-medium",
                (isCreating || showCreateForm) && "opacity-50 cursor-not-allowed"
              )}
            >
              {isCreating ? (
                <>
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  <span>{t('manager.creating')}</span>
                </>
              ) : (
                <>
                  <Plus className="w-4 h-4" />
                  <span>{t('manager.new_project')}</span>
                </>
              )}
            </button>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            <div className="relative flex-1 sm:flex-none">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
              <input
                type="search"
                placeholder={t('manager.search')}
                value={search}
                onChange={(e) => onSearchChange(e.target.value)}
                autoComplete="off"
                className="w-full sm:w-56 pl-10 pr-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 text-sm"
              />
            </div>

            <button
              type="button"
              onClick={() => onRefresh()}
              disabled={isRefreshing}
              title={t('manager.refresh_title')}
              className={cn(
                'p-2 text-gray-400 hover:text-gray-600 transition-colors rounded-md shrink-0',
                'disabled:opacity-50 disabled:cursor-not-allowed',
                'hover:bg-gray-50'
              )}
              aria-label={t('manager.refresh_title')}
            >
              <RefreshCw className={cn('w-5 h-5', isRefreshing && 'animate-spin')} />
            </button>

            {/* sm+ 专用：新项目按钮在搜索栏右侧 */}
            <button
              onClick={() => setShowCreateForm(true)}
              disabled={isCreating}
              className={cn(
                "hidden sm:flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-md",
                "hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2",
                "transition-colors text-sm font-medium",
                (isCreating || showCreateForm) && "opacity-50 cursor-not-allowed"
              )}
            >
              {isCreating ? (
                <>
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  <span>{t('manager.creating')}</span>
                </>
              ) : (
                <>
                  <Plus className="w-4 h-4" />
                  <span>{t('manager.new_project')}</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {showCreateForm && (
        <div className="p-6 border-b border-gray-200 bg-gray-50">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label
                  htmlFor="project-name"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t('manager.project_name_label')}
                </label>
                <input
                  id="project-name"
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder={t('manager.project_name_placeholder')}
                  className={cn(
                    "w-full px-3 py-2 border rounded-md shadow-sm",
                    "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                    "transition-colors text-sm",
                    errors.name ? "border-red-300" : "border-gray-300"
                  )}
                  maxLength={100}
                />
                {errors.name && (
                  <p className="text-red-600 text-xs mt-1">{errors.name}</p>
                )}
              </div>

              <div className="flex items-end">
                <span className="text-xs text-gray-500">
                  {projectName.length}/100 {t('manager.chars')}
                </span>
              </div>
            </div>

            <div>
              <div className="flex items-center gap-1 mb-1">
                <label
                  htmlFor="research-query"
                  className="block text-sm font-medium text-gray-700"
                >
                  {t('manager.research_query_label')}
                </label>
                {/* PubMed syntax help toggle. */}
                <button
                  type="button"
                  onClick={() => setShowPubMedHelp(v => !v)}
                  aria-label={t('manager.pubmed_syntax_title')}
                  aria-expanded={showPubMedHelp}
                  className="p-0.5 text-gray-400 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-300 rounded"
                >
                  <HelpCircle className="w-4 h-4" />
                </button>
              </div>
              {/* PubMed syntax cheat sheet block. */}
              {showPubMedHelp && (
                <div className="text-xs bg-blue-50 border border-blue-200 rounded p-2 mb-2 text-gray-700 space-y-1">
                  <p className="font-medium">{t('manager.pubmed_syntax_title')}</p>
                  <ul className="list-disc pl-4 space-y-0.5">
                    <li>{t('manager.pubmed_syntax_fields')}</li>
                    <li>{t('manager.pubmed_syntax_boolean')}</li>
                    <li>{t('manager.pubmed_syntax_phrase')}</li>
                  </ul>
                  <p className="font-medium pt-1">{t('manager.pubmed_syntax_examples_label')}</p>
                  <ul className="list-disc pl-4 space-y-0.5 font-mono">
                    <li>{t('manager.pubmed_syntax_example_1')}</li>
                    <li>{t('manager.pubmed_syntax_example_2')}</li>
                  </ul>
                </div>
              )}
              <textarea
                id="research-query"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t('manager.query_placeholder')}
                className={cn(
                  "w-full px-3 py-2 border rounded-md shadow-sm",
                  "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                  "transition-colors text-sm resize-none",
                  errors.query ? "border-red-300" : "border-gray-300"
                )}
                rows={3}
                maxLength={2000}
              />
              <div className="flex justify-between items-center mt-1">
                {errors.query ? (
                  <p className="text-red-600 text-xs">{errors.query}</p>
                ) : (
                  <p className="text-xs text-gray-500">
                    {t('manager.query_desc')}
                  </p>
                )}
                <span className="text-xs text-gray-500">
                  {query.length}/2000 {t('manager.chars')}
                </span>
              </div>
              {/* decision A2: warn — don't block — when user
                  uses both inline [dp] syntax and the date picker. */}
              {showDateConflict && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 mt-1">
                  {t('manager.date_conflict_warning')}
                </p>
              )}
            </div>

            <div>
              <label
                htmlFor="max-papers"
                className="block text-sm font-medium text-gray-700 mb-1"
              >
                {t('manager.max_papers_label')}
              </label>
              <div className="flex items-center space-x-4">
                <input
                  id="max-papers"
                  type="number"
                  value={maxPapers}
                  onChange={(e) => setMaxPapers(Math.max(1, Math.min(5000, parseInt(e.target.value) || 10)))}
                  min="1"
                  max="5000"
                  className={cn(
                    "w-32 px-3 py-2 border rounded-md shadow-sm",
                    "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                    "transition-colors text-sm",
                    "border-gray-300"
                  )}
                />
                <span className="text-xs text-gray-500">
                  {t('manager.limit_note')}
                </span>
              </div>
            </div>

            {/* optional publication date range filter.
                Empty inputs → backend uses Python-level defaults. */}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('manager.date_range_label')}
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="date-start"
                  type="date"
                  value={dateStart}
                  onChange={(e) => setDateStart(e.target.value)}
                  aria-label={t('manager.date_start_label')}
                  className={cn(
                    "px-3 py-2 border rounded-md shadow-sm",
                    "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                    "transition-colors text-sm",
                    errors.dateRange ? "border-red-300" : "border-gray-300"
                  )}
                />
                <span className="text-xs text-gray-500">—</span>
                <input
                  id="date-end"
                  type="date"
                  value={dateEnd}
                  onChange={(e) => setDateEnd(e.target.value)}
                  aria-label={t('manager.date_end_label')}
                  className={cn(
                    "px-3 py-2 border rounded-md shadow-sm",
                    "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                    "transition-colors text-sm",
                    errors.dateRange ? "border-red-300" : "border-gray-300"
                  )}
                />
                <span className="text-xs text-gray-500">{t('manager.date_range_hint')}</span>
              </div>
              {errors.dateRange && (
                <p className="text-red-600 text-xs mt-1">{errors.dateRange}</p>
              )}
            </div>

            {/* Advanced Options folding region containing the
                B4 escape-hatch checkbox (force-reanalyze). Default collapsed
                so the common path stays uncluttered. */}
            <div>
              <button
                type="button"
                onClick={() => setShowAdvanced(v => !v)}
                aria-expanded={showAdvanced}
                className="text-sm font-medium text-gray-700 hover:text-gray-900 focus:outline-none focus:underline"
              >
                {showAdvanced ? '▾ ' : '▸ '}{t('manager.advanced_options_label')}
              </button>
              {showAdvanced && (
                <div className="mt-2 pl-4 border-l-2 border-gray-200 space-y-2">
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={forceReanalyze}
                      onChange={(e) => setForceReanalyze(e.target.checked)}
                      className="mt-1"
                    />
                    <div>
                      <span className="text-sm text-gray-800">{t('manager.force_reanalyze_label')}</span>
                      <p className="text-xs text-gray-500 mt-0.5">{t('manager.force_reanalyze_desc')}</p>
                    </div>
                  </label>
                  {/* MeSH expansion toggle. Default ON; allow
                      power users (already writing PubMed syntax) to disable. */}
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={meshExpansion}
                      onChange={(e) => setMeshExpansion(e.target.checked)}
                      className="mt-1"
                    />
                    <div>
                      <span className="text-sm text-gray-800">{t('manager.mesh_expansion_label')}</span>
                      <p className="text-xs text-gray-500 mt-0.5">{t('manager.mesh_expansion_desc')}</p>
                    </div>
                  </label>
                </div>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label
                  htmlFor="analysis-model"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t('manager.analysis_model_label')}
                </label>
                {modelsLoading ? (
                  <div className="flex items-center space-x-2 py-2">
                    <div className="w-4 h-4 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin" />
                    <span className="text-xs text-gray-500">{t('common.loading')}</span>
                  </div>
                ) : modelsError ? (
                  <p className="text-xs text-red-500">{modelsError}</p>
                ) : (
                  <select
                    id="analysis-model"
                    value={analysisModel}
                    onChange={(e) => setAnalysisModel(e.target.value)}
                    disabled={modelsLoading}
                    className={cn(
                      "w-full px-3 py-2 border rounded-md shadow-sm",
                      "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                      "transition-colors text-sm bg-white text-gray-800",
                      "border-gray-300"
                    )}
                  >
                    <option value="">
                      {modelDefaults.analysis
                        ? t('manager.model_default_option', { model: modelDefaults.analysis })
                        : t('manager.model_default_option_unknown')}
                    </option>
                    {modelOptions.map((m) => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </select>
                )}
              </div>

              <div>
                <label
                  htmlFor="download-model"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  {t('manager.download_model_label')}
                </label>
                {modelsLoading ? (
                  <div className="flex items-center space-x-2 py-2">
                    <div className="w-4 h-4 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin" />
                    <span className="text-xs text-gray-500">{t('common.loading')}</span>
                  </div>
                ) : modelsError ? (
                  <p className="text-xs text-red-500">{modelsError}</p>
                ) : (
                  <select
                    id="download-model"
                    value={downloadModel}
                    onChange={(e) => setDownloadModel(e.target.value)}
                    disabled={modelsLoading}
                    className={cn(
                      "w-full px-3 py-2 border rounded-md shadow-sm",
                      "focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100",
                      "transition-colors text-sm bg-white text-gray-800",
                      "border-gray-300"
                    )}
                  >
                    <option value="">
                      {modelDefaults.download
                        ? t('manager.model_default_option', { model: modelDefaults.download })
                        : t('manager.model_default_option_unknown')}
                    </option>
                    {modelOptions.map((m) => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                  </select>
                )}
              </div>
            </div>

            <div className="flex justify-end space-x-3 pt-4">
              <button
                type="button"
                onClick={handleCancel}
                disabled={isCreating}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-50 focus:outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 transition-colors"
              >
                {t('manager.cancel')}
              </button>

              <button
                type="submit"
                disabled={isCreating || !projectName.trim() || !query.trim()}
                className={cn(
                  "px-4 py-2 text-sm font-medium text-white",
                  "bg-blue-600 border border-transparent rounded-md",
                  "hover:bg-blue-700 focus:ring-2 focus:ring-blue-500 focus:ring-offset-2",
                  "transition-colors",
                  (isCreating || !projectName.trim() || !query.trim()) &&
                  "opacity-50 cursor-not-allowed"
                )}
              >
                {t('manager.create_project')}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="p-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
          {statusCards.map(({ status, count, bgClass, selectedBgClass, selectedBorderClass, textClass }) => {
            const isSelected = statusFilter === status;
            return (
              <button
                key={status}
                type="button"
                onClick={() => onStatusFilterChange(isSelected ? '' : status)}
                className={cn(
                  'p-4 rounded-lg text-left border transition-colors',
                  isSelected ? selectedBgClass : bgClass,
                  isSelected
                    ? selectedBorderClass
                    : 'border-transparent hover:border-gray-300'
                )}
                title={isSelected ? t('manager.status_all') : t(`status.${status}`)}
              >
                <div className={cn('text-2xl font-bold', textClass)}>{count}</div>
                <div className={cn('text-sm', textClass)}>{t(`status.${status}`)}</div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
};
