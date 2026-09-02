// Root application component with routing and authentication gating.

import { lazy, Suspense, useRef, useState } from 'react';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { CircleUser, Wifi, WifiOff, AlertTriangle, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react';
import { useAuth } from './contexts/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { ForgotPasswordPage } from './pages/ForgotPasswordPage';
import { ResetPasswordPage } from './pages/ResetPasswordPage';
import { useProjects } from './hooks/useProjects';
import { ProjectManager } from './components/ProjectManager';
import { ProjectGrid } from './components/ProjectGrid';
import { InputModal } from './components/InputModal';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ThinkingDisplay } from './components/ThinkingDisplay';
import { UserMenu } from './components/UserMenu';
import { VersionBadge } from './components/VersionBadge';
import { cn, sortProjectsByDate } from './utils';
import { translateErrorMessage } from './utils/error';
import { useTranslation } from './hooks/useTranslation';

// Route-level lazy loading — these pages are not needed on the initial render.
const ProjectDetail = lazy(() => import('./pages/ProjectDetail').then(m => ({ default: m.ProjectDetail })));
const AccountPage   = lazy(() => import('./pages/AccountPage').then(m => ({ default: m.AccountPage })));
const AdminPage     = lazy(() => import('./pages/AdminPage').then(m => ({ default: m.AdminPage })));
// NotFoundPage replaces the deleted cross-project ReviewQueuePage
// and also catches any other unmatched URL (was silently rendering an empty page).
const NotFoundPage  = lazy(() => import('./pages/NotFoundPage').then(m => ({ default: m.NotFoundPage })));

// Build the page-number list with ellipsis: always show first/last and a
// window of ±1 around the current page. Total ≤ 7: render all; otherwise
// the result has 7 slots max so it fits a phone row.
function getPageList(current: number, total: number): (number | '...')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out: (number | '...')[] = [1];
  if (current > 4) out.push('...');
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  for (let i = start; i <= end; i++) out.push(i);
  if (current < total - 3) out.push('...');
  out.push(total);
  return out;
}

function HomePage() {
  const { email } = useAuth();
  const navigate = useNavigate();
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const userButtonRef = useRef<HTMLButtonElement>(null);
  const { t } = useTranslation();
  const {
    projects,
    totalProjects,
    page,
    setPage,
    totalPages,
    pageSize,
    searchInput,
    setSearchInput,
    statusFilter,
    setStatusFilter,
    projectStats,
    currentInputRequest,
    currentThinking,
    isLoading,
    isFetchingProjects,
    isCreatingProject,
    isSubmittingInput,
    isSocketConnected,
    error,
    createProjectError,
    submitInputError,
    createProject,
    submitInputResponse,
    joinProject,
    closeInputModal,
    refetch,
    pauseProject,
    resumeProject,
    cancelProject,
  } = useProjects();

  const sortedProjects = sortProjectsByDate(projects);

  const handleSelectProject = (project: any) => {
    joinProject(project.id);
    navigate(`/project/${project.id}`);
  };

  const ErrorAlert = ({ error, title }: { error: Error; title: string }) => (
    <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
      <div className="flex items-center space-x-2">
        <AlertTriangle className="w-5 h-5 text-red-500" />
        <h3 className="text-sm font-medium text-red-800">{title}</h3>
      </div>
      <p className="text-sm text-red-700 mt-1">{translateErrorMessage(error.message, t, 'common.error')}</p>
      <button
        onClick={() => refetch()}
        className="text-sm text-red-800 underline hover:text-red-900 mt-2"
      >
        {t('error.retry')}
      </button>
    </div>
  );

  return (
    <ErrorBoundary>
      <div className="min-h-screen bg-gray-50">
      <div className="bg-white border-b border-gray-200 px-4 sm:px-6 py-3">
        <div className="flex flex-wrap items-center justify-end gap-2 sm:gap-3">
          <VersionBadge />
          <div className={cn(
            "flex items-center gap-1.5 px-2 sm:px-3 py-1 rounded-lg text-sm",
            isSocketConnected ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
          )}>
            {isSocketConnected ? (
              <>
                <Wifi className="w-4 h-4" />
                <span className="hidden sm:inline">{t('connection.connected')}</span>
              </>
            ) : (
              <>
                <WifiOff className="w-4 h-4" />
                <span className="hidden sm:inline">{t('connection.disconnected')}</span>
              </>
            )}
          </div>

          <div className="relative">
            <button
              ref={userButtonRef}
              onClick={() => setIsUserMenuOpen(o => !o)}
              className="w-9 h-9 rounded-full flex items-center justify-center text-gray-500 hover:bg-gray-100 transition-colors"
              aria-label={`User menu for ${email}`}
            >
              <CircleUser className="w-7 h-7" />
            </button>
            <UserMenu
              isOpen={isUserMenuOpen}
              onClose={() => setIsUserMenuOpen(false)}
            />
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        <div className="space-y-8">
          {error && (
            <ErrorAlert error={error} title={t('error.auth')} />
          )}

          {createProjectError && (
            <ErrorAlert error={createProjectError} title={t('common.error')} />
          )}

          {submitInputError && (
            <ErrorAlert error={submitInputError} title={t('common.error')} />
          )}

          <ProjectManager
            onCreateProject={createProject}
            isCreating={isCreatingProject}
            projectStats={projectStats}
            search={searchInput}
            onSearchChange={setSearchInput}
            statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter}
            onRefresh={refetch}
            isRefreshing={isFetchingProjects}
          />

          {currentThinking && (
            <ThinkingDisplay
              thinking={currentThinking.thinking}
              agentName={currentThinking.agentName}
            />
          )}

          {isLoading ? (
            <div className="flex items-center justify-center h-64">
              <div className="flex items-center space-x-3">
                <div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
                <span className="text-gray-600">{t('common.loading')}</span>
              </div>
            </div>
          ) : (
            <ProjectGrid
              projects={sortedProjects}
              onSelectProject={handleSelectProject}
              onPauseProject={pauseProject}
              onResumeProject={resumeProject}
              onCancelProject={cancelProject}
            />
          )}

          {!isLoading && totalProjects > 0 && (
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-1 py-2">
              <p className="text-sm text-gray-600">
                {t('manager.showing_range', {
                  from: (page - 1) * pageSize + 1,
                  to: Math.min(page * pageSize, totalProjects),
                  total: totalProjects,
                })}
              </p>
              <div className="flex flex-wrap items-center gap-1">
                {/* First/last hidden on xs to save row width — prev/next + numbered buttons cover daily use */}
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage(1)}
                  aria-label="First page"
                  className="hidden sm:inline-flex items-center justify-center w-9 h-9 rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <ChevronsLeft className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  aria-label={t('manager.page_prev')}
                  className="inline-flex items-center justify-center w-8 h-8 sm:w-9 sm:h-9 rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                {getPageList(page, totalPages).map((p, i) =>
                  p === '...' ? (
                    <span key={`gap-${i}`} className="px-1 text-xs sm:text-sm text-gray-400 select-none">…</span>
                  ) : (
                    <button
                      key={p}
                      type="button"
                      onClick={() => setPage(p)}
                      aria-current={p === page ? 'page' : undefined}
                      className={cn(
                        'inline-flex items-center justify-center min-w-[32px] sm:min-w-[36px] h-8 sm:h-9 px-2 text-xs sm:text-sm rounded-md border tabular-nums',
                        p === page
                          ? 'border-blue-500 bg-blue-50 text-blue-700 font-semibold'
                          : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                      )}
                    >
                      {p}
                    </button>
                  )
                )}
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  aria-label={t('manager.page_next')}
                  className="inline-flex items-center justify-center w-8 h-8 sm:w-9 sm:h-9 rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  disabled={page >= totalPages}
                  onClick={() => setPage(totalPages)}
                  aria-label="Last page"
                  className="hidden sm:inline-flex items-center justify-center w-9 h-9 rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <ChevronsRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      <InputModal
        isOpen={!!currentInputRequest}
        inputRequest={currentInputRequest || undefined}
        onSubmit={submitInputResponse}
        onClose={closeInputModal}
      />

      {isSubmittingInput && (
        <div className="fixed inset-0 bg-black bg-opacity-25 z-30 flex items-center justify-center">
          <div className="bg-white rounded-lg p-6 shadow-xl">
            <div className="flex items-center space-x-3">
              <div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
              <span className="text-gray-700">{t('input.submit')}</span>
            </div>
          </div>
        </div>
      )}
      </div>
    </ErrorBoundary>
  );
}

function App() {
  const { isAuthenticated, isLoading } = useAuth();
  const { t } = useTranslation();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="flex items-center space-x-3">
          <div className="w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
          <span className="text-gray-600">{t('common.loading')}</span>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }

  return (
    <ErrorBoundary>
      <Suspense fallback={null}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/project/:projectId" element={<ProjectDetail />} />
          {/* /review-queue route removed (consolidated into
              ProjectDetail's Review tab). NotFoundPage catches it plus any
              other unmatched URL — previously these silently rendered empty. */}
          <Route path="/account" element={<AccountPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}

export default App;
