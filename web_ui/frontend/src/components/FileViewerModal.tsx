// Modal for viewing artifact file content (PDFs, markdown, plain text).

import { X, FileText } from 'lucide-react';
import { useEffect, useState } from 'react';
import { databaseApi } from '../services/databaseApi';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTranslation } from '../hooks/useTranslation';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

interface FileViewerModalProps {
  artifactId: number;
  fileName: string;
  onClose: () => void;
}

export function FileViewerModal({ artifactId, fileName, onClose }: FileViewerModalProps) {
  const { t } = useTranslation();
  const [content, setContent] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const displayFileName = (() => {
    try {
      return decodeURIComponent(fileName);
    } catch {
      return fileName;
    }
  })();

  useEffect(() => {
    const loadContent = async () => {
      try {
        setIsLoading(true);
        const contentText = await databaseApi.getArtifactContentText(artifactId);
        setContent(contentText);
      } catch (err) {
        setError(err as Error);
      } finally {
        setIsLoading(false);
      }
    };
    loadContent();
  }, [artifactId]);

  const getFileExtension = () => {
    const ext = fileName.split('.').pop()?.toLowerCase();
    return ext || '';
  };

  const renderContent = () => {
    if (!content) {
      return (
        <div className="text-center py-8 text-gray-500">
          {t('file.no_content')}
        </div>
      );
    }

    const ext = getFileExtension();
    const isLikelyMarkdown =
      ['md', 'markdown', 'mdx'].includes(ext)
      || /^#{1,6}\s/m.test(content)
      || /\n[-*]\s+/.test(content)
      || /\|.*\|/.test(content);

    if (ext === 'pdf' && content === '[PDF_FILE]') {
      return (
        <iframe
          src={`${API_URL}/api/database/artifacts/${artifactId}/file?token=${sessionStorage.getItem('auth_token')}`}
          className="w-full h-full min-h-[600px] border-0"
          title={displayFileName}
        />
      );
    }

    if (content.startsWith('[PDF File')) {
      return (
        <div className="text-center py-8">
          <p className="text-gray-600 mb-4">{t('file.pdf_old_format')}</p>
          <p className="text-sm text-gray-500">{t('file.pdf_migration_hint')}</p>
        </div>
      );
    }

    if (isLikelyMarkdown) {
      return (
        <div className="prose prose-sm md:prose-base max-w-none text-gray-800 prose-headings:text-gray-900 prose-headings:font-semibold prose-p:leading-7 prose-p:my-3 prose-li:my-1 prose-strong:text-gray-900 prose-a:text-blue-600 hover:prose-a:text-blue-700 prose-blockquote:border-l-4 prose-blockquote:border-blue-200 prose-blockquote:bg-blue-50/50 prose-blockquote:py-1 prose-blockquote:px-3 prose-hr:border-gray-200 prose-table:w-full prose-th:bg-gray-50 prose-th:font-semibold prose-th:text-gray-700 prose-th:border prose-th:border-gray-200 prose-td:border prose-td:border-gray-200 prose-td:align-top prose-img:rounded-md [&_*]:break-words [&_*]:[overflow-wrap:anywhere] [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_code]:break-all">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      );
    }

    return (
      <pre className="whitespace-pre-wrap break-words [overflow-wrap:anywhere] font-mono text-sm text-gray-800">
        {content}
      </pre>
    );
  };

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="flex items-center space-x-2 flex-1 min-w-0 mr-4">
            <FileText className="w-5 h-5 text-blue-600 flex-shrink-0" />
            <h2 className="text-lg font-semibold text-gray-900 break-all whitespace-normal overflow-hidden">
              {displayFileName}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors flex-shrink-0"
            aria-label={t('file.close_btn')}
          >
            <X className="w-5 h-5 text-gray-600" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6 break-words [overflow-wrap:anywhere]">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="flex items-center space-x-3">
                <div className="w-6 h-6 border-3 border-blue-600 border-t-transparent rounded-full animate-spin" />
                <span className="text-gray-700">{t('file.loading')}</span>
              </div>
            </div>
          ) : error ? (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-red-700">{t('file.load_failed')}: {error.message}</p>
            </div>
          ) : (
            renderContent()
          )}
        </div>

        <div className="flex justify-end p-4 border-t border-gray-200">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700 transition-colors"
          >
            {t('file.close_btn')}
          </button>
        </div>
      </div>
    </div>
  );
}
