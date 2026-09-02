// Login page with forgot password link (registration is disabled).

import React, { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { AlertTriangle, Eye, EyeOff } from 'lucide-react';
import { cn } from '../utils';
import { useTranslation } from '../hooks/useTranslation';
import { Link } from 'react-router-dom';

const AUTH_ERROR_TRANSLATIONS: Record<string, string> = {
  'Invalid email or password': 'login.error',
  'Invalid credentials': 'login.error',
  'Account is disabled': 'login.error_disabled',
  'Failed to connect to server': 'error.network',
};

function translateAuthError(error: string, t: (key: string) => string): string {
  const key = AUTH_ERROR_TRANSLATIONS[error];
  return key ? t(key) : error;
}

export function LoginPage() {
  const { login } = useAuth();
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    setIsSubmitting(true);
    const result = await login(email, password);
    if (!result.success) {
      const translatedError = translateAuthError(result.error || '', t);
      setError(translatedError || t('error.login_failed'));
    }
    setIsSubmitting(false);
  };

  return (
    <div className="min-h-screen grid grid-cols-1 lg:grid-cols-2">
      <div className="flex items-center justify-center bg-white px-8 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8">
            <div className="flex items-center gap-2 mb-6">
              <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
                <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5 text-white" stroke="currentColor" strokeWidth={2}>
                  <path d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2v-4M9 21H5a2 2 0 01-2-2v-4m0 0h18" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <span className="text-sm font-semibold text-gray-700 tracking-wide">{t('login.brand')}</span>
            </div>

            <h1 className="text-3xl font-bold text-gray-900">
              {t('login.welcome_back')}
            </h1>
            <p className="text-gray-500 mt-1 text-sm">
              {t('login.welcome_subtitle')}
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

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                {t('login.email_label')}
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={t('login.email_placeholder')}
                required
                autoComplete="email"
                autoFocus
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1">
                <label htmlFor="password" className="block text-sm font-medium text-gray-700">
                  {t('login.password_label')}
                </label>
                <Link
                  to="/forgot-password"
                  className="text-xs text-blue-600 hover:text-blue-700 font-medium"
                >
                  {t('login.forgot_password')}
                </Link>
              </div>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={t('login.password_placeholder')}
                  required
                  autoComplete="current-password"
                  className="w-full px-3 py-2.5 pr-10 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                  tabIndex={-1}
                  aria-label={showPassword ? t('login.hide_password') : t('login.show_password')}
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isSubmitting || !email.trim() || !password.trim()}
              className={cn(
                'w-full flex items-center justify-center py-2.5 px-4 rounded-lg text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors mt-2',
                isSubmitting || !email.trim() || !password.trim()
                  ? 'opacity-50 cursor-not-allowed'
                  : ''
              )}
            >
              {isSubmitting ? (
                <>
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />
                  {t('login.signing_in')}
                </>
              ) : (
                t('login.login_btn')
              )}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-gray-500">
            {t('login.contact_admin')}
          </p>
        </div>
      </div>

      <div className="hidden lg:flex flex-col items-center justify-center bg-gradient-to-br from-blue-600 to-indigo-700 px-8 text-white relative overflow-hidden">
        <div className="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-white/5" />
        <div className="absolute -bottom-32 -left-16 w-80 h-80 rounded-full bg-white/5" />
        <div className="absolute top-1/3 right-8 w-48 h-48 rounded-full bg-white/5" />

        <div className="relative z-10 text-center max-w-md">
          <div className="flex justify-center mb-6">
            <div className="w-20 h-20 rounded-2xl bg-white/15 flex items-center justify-center backdrop-blur-sm border border-white/20">
              <svg viewBox="0 0 24 24" fill="none" className="w-10 h-10 text-white" stroke="currentColor" strokeWidth={1.5}>
                <path d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </div>

          <h2 className="text-2xl font-bold leading-tight mb-4">
            {t('login.ai_powered_title')}
          </h2>
          <p className="text-blue-100 text-base leading-relaxed mb-10">
            {t('login.ai_powered_desc')}
          </p>

          <div className="flex flex-col gap-3 text-left">
            {[
              { icon: '🔍', label: t('login.find_title'), desc: t('login.find_desc') },
              { icon: '🧬', label: t('login.analyze_title'), desc: t('login.analyze_desc') },
              { icon: '⬇️', label: t('login.download_title'), desc: t('login.download_desc') },
            ].map(({ icon, label, desc }) => (
              <div
                key={label}
                className="flex items-center gap-3 bg-white/10 rounded-xl px-4 py-3 border border-white/15"
              >
                <span className="text-xl leading-none">{icon}</span>
                <div>
                  <p className="text-sm font-semibold">{label}</p>
                  <p className="text-xs text-blue-200">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
