// lightweight 404 page mounted as the authenticated catch-all
// route. Replaces both the deleted standalone /review-queue page and the
// previous behavior of silently rendering an empty layout for any unmatched
// URL. SPA can't return a real HTTP 404 status, so this is the semantic
// equivalent — a clear message + a link back to the home page.

import React from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowLeft } from 'lucide-react';
import { useTranslation } from '../hooks/useTranslation';

export const NotFoundPage: React.FC = () => {
  const { t } = useTranslation();
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="max-w-md w-full bg-white border border-gray-200 rounded-lg shadow-sm p-8 text-center">
        <div className="flex justify-center mb-4">
          <AlertTriangle className="w-12 h-12 text-amber-500" />
        </div>
        <h1 className="text-2xl font-semibold text-gray-900 mb-2">
          {t('not_found.title')}
        </h1>
        <p className="text-sm text-gray-600 mb-6">
          {t('not_found.message')}
        </p>
        <Link
          to="/"
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors text-sm"
        >
          <ArrowLeft className="w-4 h-4" />
          {t('not_found.back_home')}
        </Link>
      </div>
    </div>
  );
};
