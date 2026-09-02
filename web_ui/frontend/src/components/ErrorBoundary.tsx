// React error boundary that catches render errors and displays a recovery UI.

import { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { t } from '../i18n';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null
  };

  public static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      error,
      errorInfo: null,
    };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Error caught by boundary:', error, errorInfo);
    this.setState({
      error,
      errorInfo,
    });
  }

  private handleReload = () => {
    window.location.reload();
  };

  private handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    });
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-lg shadow-lg p-8 max-w-2xl w-full">
            <div className="flex items-center space-x-3 mb-6">
              <AlertTriangle className="w-8 h-8 text-red-500" />
              <div>
                <h1 className="text-xl font-semibold text-gray-900">
                  Something went wrong
                </h1>
                <p className="text-sm text-gray-600">
                  {t('error.boundary.message')}
                </p>
              </div>
            </div>

            {this.state.error && (
              <div className="mb-6">
                <h2 className="text-sm font-medium text-gray-900 mb-2">
                  {t('error.boundary.details')}:
                </h2>
                <div className="bg-red-50 border border-red-200 rounded-md p-4">
                  <code className="text-sm text-red-800 font-mono">
                    {this.state.error.message}
                  </code>
                </div>
              </div>
            )}

            {this.state.errorInfo && (
              <div className="mb-6">
                <h2 className="text-sm font-medium text-gray-900 mb-2">
                  {t('error.boundary.stack')}:
                </h2>
                <div className="bg-gray-50 border border-gray-200 rounded-md p-4 max-h-40 overflow-y-auto">
                  <pre className="text-xs text-gray-700 font-mono whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                    {this.state.errorInfo.componentStack}
                  </pre>
                </div>
              </div>
            )}

            <div className="flex space-x-3">
              <button
                onClick={this.handleReset}
                className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors"
              >
                <RefreshCw className="w-4 h-4" />
                <span>{t('error.boundary.try_again')}</span>
              </button>

              <button
                onClick={this.handleReload}
                className="flex items-center space-x-2 px-4 py-2 bg-gray-600 text-white rounded-md hover:bg-gray-700 transition-colors"
              >
                <RefreshCw className="w-4 h-4" />
                <span>{t('error.boundary.reload')}</span>
              </button>
            </div>

            <div className="mt-6 pt-6 border-t border-gray-200">
              <p className="text-xs text-gray-500">
                {t('error.boundary.hint')}
              </p>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
