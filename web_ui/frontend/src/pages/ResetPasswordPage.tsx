// Reset password page - set new password using token from email.

import React, { useState, useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, ArrowLeft, Eye, EyeOff, CheckCircle } from 'lucide-react';
import { useTranslation } from '../hooks/useTranslation';
import { api } from '../utils/api';
import { cn } from '../utils';
import { translateErrorMessage } from '../utils/error';

export function ResetPasswordPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setError(t('reset.error'));
    }
  }, [token, t]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!token) {
      setError(t('reset.error'));
      return;
    }

    if (password.length < 8) {
      setError(t('error.password_length'));
      return;
    }

    if (password !== confirmPassword) {
      setError(t('reset.password_mismatch'));
      return;
    }

    setIsSubmitting(true);
    try {
      await api.post(`/api/auth/reset-password/${token}`, { new_password: password });
      setSuccess(true);
      setTimeout(() => {
        navigate('/login');
      }, 3000);
    } catch (err: any) {
      setError(translateErrorMessage(err?.response?.data?.detail || err?.message, t, 'reset.error'));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (success) {
    return (
      <div className="min-h-screen grid grid-cols-1 lg:grid-cols-2">
        <div className="flex items-center justify-center bg-white px-8 py-12">
          <div className="w-full max-w-sm text-center">
            <div className="flex justify-center mb-6">
              <div className="w-20 h-20 rounded-full bg-green-100 flex items-center justify-center">
                <CheckCircle className="w-10 h-10 text-green-600" />
              </div>
            </div>
            <h1 className="text-2xl font-bold text-gray-900 mb-2">
              {t('reset.success')}
            </h1>
          </div>
        </div>
        <div className="hidden lg:flex items-center justify-center bg-gradient-to-br from-blue-600 to-indigo-700 px-12">
          <div className="text-white text-center">
            <h2 className="text-3xl font-bold">{t('login.ai_powered_title')}</h2>
          </div>
        </div>
      </div>
    );
  }

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
              {t('reset.title')}
            </h1>
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
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
                {t('reset.new_password_label')}
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={t('reset.new_password_placeholder')}
                  required
                  autoComplete="new-password"
                  className="w-full px-3 py-2.5 pr-10 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <div>
              <label htmlFor="confirmPassword" className="block text-sm font-medium text-gray-700 mb-1">
                {t('reset.confirm_password_label')}
              </label>
              <input
                id="confirmPassword"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder={t('reset.confirm_password_placeholder')}
                required
                autoComplete="new-password"
                className="w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-all"
              />
            </div>

            <button
              type="submit"
              disabled={isSubmitting || !token || password.length < 8}
              className={cn(
                'w-full flex items-center justify-center py-2.5 px-4 rounded-lg text-sm font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors mt-2',
                (isSubmitting || !token || password.length < 8) && 'opacity-50 cursor-not-allowed'
              )}
            >
              {isSubmitting ? (
                <>
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin mr-2" />
                  {t('forgot.sending')}
                </>
              ) : (
                t('reset.submit')
              )}
            </button>
          </form>
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
