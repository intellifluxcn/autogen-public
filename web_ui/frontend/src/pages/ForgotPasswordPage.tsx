// Forgot password page - request password reset email.

import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowLeft } from 'lucide-react';
import { useTranslation } from '../hooks/useTranslation';
import { api } from '../utils/api';

export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    if (!email.trim()) {
      setError(t('error.name_required'));
      return;
    }

    setIsSubmitting(true);
    try {
      await api.post('/api/auth/reset-password', { email });
      setSuccess(true);
    } catch (err: any) {
      // Even on error, show success to prevent email enumeration
      setSuccess(true);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen grid grid-cols-1 lg:grid-cols-2">
      <div className="flex items-center justify-center bg-white px-8 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8">
            <Link
              to="/login"
              className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 mb-6"
            >
              <ArrowLeft className="w-4 h-4" />
              {t('forgot.back_login')}
            </Link>

            <div className="flex items-center gap-2 mb-6">
              <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
                <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5 text-white" stroke="currentColor" strokeWidth={2}>
                  <path d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2v-4M9 21H5a2 2 0 01-2-2v-4m0 0h18" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <span className="text-sm font-semibold text-gray-700 tracking-wide">{t('login.brand')}</span>
            </div>

            <h1 className="text-3xl font-bold text-gray-900">
              {t('forgot.title')}
            </h1>
            <p className="text-gray-500 mt-1 text-sm">
              {t('forgot.subtitle')}
            </p>
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-5">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-red-500 flex-shrink-0" />
                <p className="text-sm text-red-700">{error}</p>
              </div>
            </div>
          )}

          {success && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-3 mb-5">
              <p className="text-sm text-green-700">{t('forgot.success')}</p>
            </div>
          )}

          {!success && (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                  {t('forgot.email_label')}
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t('forgot.email_placeholder')}
                  required
                  autoComplete="email"
                  autoFocus
                  className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
                />
              </div>

              <button
                type="submit"
                disabled={isSubmitting || !email.trim()}
                className="w-full flex items-center justify-center py-2.5 px-4 rounded-lg text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors mt-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSubmitting ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />
                    {t('forgot.sending')}
                  </>
                ) : (
                  t('forgot.submit')
                )}
              </button>
            </form>
          )}
        </div>
      </div>

      <div className="hidden lg:flex flex-col items-center justify-center bg-gradient-to-br from-blue-600 to-indigo-700 px-12 text-white relative overflow-hidden">
        <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-white/5" />
        <div className="absolute -bottom-32 -left-16 w-80 h-80 rounded-full bg-white/5" />
        <div className="absolute top-1/3 right-8 w-48 h-48 rounded-full bg-white/5" />

        <div className="relative z-10 text-center max-w-sm">
          <div className="flex justify-center mb-6">
            <div className="w-20 h-20 rounded-2xl bg-white/15 flex items-center justify-center backdrop-blur-sm border border-white/20">
              <svg viewBox="0 0 24 24" fill="none" className="w-10 h-10 text-white" stroke="currentColor" strokeWidth={1.5}>
                <path d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </div>

          <h2 className="text-3xl font-bold leading-tight mb-4">
            {t('login.ai_powered_title')}
          </h2>
          <p className="text-blue-100 text-base leading-relaxed">
            {t('login.ai_powered_desc')}
          </p>
        </div>
      </div>
    </div>
  );
}
