// Admin page — user management for administrators.

import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Plus, Pencil, KeyRound, Trash2,
  AlertTriangle, CheckCircle, Eye, EyeOff, Search,
} from 'lucide-react';
import { useTranslation } from '../hooks/useTranslation';
import { useAuth } from '../contexts/AuthContext';
import { apiClient } from '../utils/api';
import { translateErrorMessage } from '../utils/error';

interface User {
  id: number;
  name: string;
  email: string;
  role: string;
  is_active: number;
  created_at: string;
}

type Mode = 'create' | 'edit';

const PASSWORD_MIN_LENGTH = 8;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function AdminPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { email: currentEmail } = useAuth();
  const me = (currentEmail || '').toLowerCase();

  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  // Create / Edit modal (shared)
  const [mode, setMode] = useState<Mode | null>(null);
  const [editingEmail, setEditingEmail] = useState<string | null>(null);
  const [formName, setFormName] = useState('');
  const [formEmail, setFormEmail] = useState('');
  const [formPassword, setFormPassword] = useState('');
  const [formRole, setFormRole] = useState('user');
  const [formIsActive, setFormIsActive] = useState(1);
  const [showFormPassword, setShowFormPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState('');

  // Reset password modal
  const [resetEmail, setResetEmail] = useState<string | null>(null);
  const [resetPassword, setResetPassword] = useState('');
  const [showResetPassword, setShowResetPassword] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [resetError, setResetError] = useState('');

  // Delete confirm
  const [deleteUserEmail, setDeleteUserEmail] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => { loadUsers(); }, []);

  // Esc closes any open modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (mode) closeForm();
      else if (resetEmail) closeReset();
      else if (deleteUserEmail) setDeleteUserEmail(null);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [mode, resetEmail, deleteUserEmail]);

  const loadUsers = async () => {
    setIsLoading(true);
    try {
      const data = await apiClient.getAdminUsers();
      setUsers(data);
    } catch (err: any) {
      setError(translateErrorMessage(err?.message, t, 'admin.error'));
    } finally {
      setIsLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      u => u.email.toLowerCase().includes(q) || (u.name || '').toLowerCase().includes(q),
    );
  }, [users, searchQuery]);

  const openCreate = () => {
    setMode('create');
    setEditingEmail(null);
    setFormName(''); setFormEmail(''); setFormPassword('');
    setFormRole('user'); setFormIsActive(1);
    setShowFormPassword(false); setFormError('');
  };

  const openEdit = (u: User) => {
    setMode('edit');
    setEditingEmail(u.email);
    setFormName(u.name); setFormEmail(u.email); setFormPassword('');
    setFormRole(u.role); setFormIsActive(u.is_active);
    setShowFormPassword(false); setFormError('');
  };

  const closeForm = () => {
    if (isSubmitting) return;
    setMode(null); setEditingEmail(null); setFormError('');
  };

  const flashSuccess = (msg: string) => {
    setSuccess(msg);
    setTimeout(() => setSuccess(''), 3000);
  };

  const submitForm = async () => {
    setFormError('');
    if (!formName.trim()) {
      setFormError(t('admin.error_name_required')); return;
    }
    if (mode === 'create') {
      if (!EMAIL_RE.test(formEmail.trim())) {
        setFormError(t('admin.error_email_invalid')); return;
      }
      if (formPassword.length < PASSWORD_MIN_LENGTH) {
        setFormError(t('admin.error_password_short')); return;
      }
    }

    setIsSubmitting(true);
    try {
      if (mode === 'create') {
        await apiClient.createAdminUser({
          name: formName.trim(),
          email: formEmail.trim().toLowerCase(),
          password: formPassword,
          role: formRole,
        });
        flashSuccess(t('admin.success'));
      } else if (mode === 'edit' && editingEmail) {
        await apiClient.updateAdminUser(editingEmail, {
          name: formName.trim(),
          role: formRole,
          is_active: formIsActive,
        });
        flashSuccess(t('admin.update_success'));
      }
      setMode(null); setEditingEmail(null);
      await loadUsers();
    } catch (err: any) {
      setFormError(translateErrorMessage(err?.message, t, 'admin.error'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const closeReset = () => {
    if (isResetting) return;
    setResetEmail(null); setResetPassword('');
    setShowResetPassword(false); setResetError('');
  };

  const submitReset = async () => {
    if (!resetEmail) return;
    if (resetPassword.length < PASSWORD_MIN_LENGTH) {
      setResetError(t('admin.error_password_short')); return;
    }
    setIsResetting(true); setResetError('');
    try {
      await apiClient.resetAdminUserPassword(resetEmail, resetPassword);
      flashSuccess(t('admin.reset_success'));
      closeReset();
    } catch (err: any) {
      setResetError(translateErrorMessage(err?.message, t, 'admin.error'));
    } finally {
      setIsResetting(false);
    }
  };

  const handleDeleteUser = async () => {
    if (!deleteUserEmail) return;
    setIsDeleting(true);
    try {
      await apiClient.deleteAdminUser(deleteUserEmail);
      flashSuccess(t('admin.delete_success'));
      setDeleteUserEmail(null);
      await loadUsers();
    } catch (err: any) {
      setError(translateErrorMessage(err?.message, t, 'admin.error'));
    } finally {
      setIsDeleting(false);
    }
  };

  const formatDate = (s: string) => {
    try { return new Date(s).toLocaleDateString(); } catch { return s; }
  };

  const isSelf = (e: string) => e.toLowerCase() === me;

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="bg-white border-b border-gray-200 px-6 py-3">
        <button
          onClick={() => navigate('/')}
          className="flex items-center gap-2 text-sm text-gray-600 hover:text-gray-900 transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>{t('common.back')}</span>
        </button>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-10">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-gray-900">{t('admin.title')}</h1>
          <button
            onClick={openCreate}
            className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
          >
            <Plus className="w-4 h-4" />
            {t('admin.add_user')}
          </button>
        </div>

        {/* Search */}
        <div className="relative mb-6">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('admin.search_placeholder')}
            autoComplete="off"
            className="w-full pl-10 pr-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
            <div className="flex items-center gap-2 text-red-700">
              <AlertTriangle className="w-5 h-5" />
              {error}
            </div>
          </div>
        )}

        {success && (
          <div className="bg-green-50 border border-green-200 rounded-lg p-4 mb-6">
            <div className="flex items-center gap-2 text-green-700">
              <CheckCircle className="w-5 h-5" />
              {success}
            </div>
          </div>
        )}

        <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
          {isLoading ? (
            <div className="p-8 text-center text-gray-500">{t('common.loading')}</div>
          ) : filtered.length === 0 ? (
            <div className="p-8 text-center text-gray-500">{t('admin.no_users')}</div>
          ) : (
            <table className="w-full min-w-[640px]">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">
                    {t('admin.name')}
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">
                    {t('admin.email')}
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">
                    {t('admin.role')}
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">
                    {t('admin.status')}
                  </th>
                  <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase">
                    {t('admin.created')}
                  </th>
                  <th className="text-right px-4 py-3 text-xs font-medium text-gray-500 uppercase">
                    {t('admin.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {filtered.map((user) => {
                  const self = isSelf(user.email);
                  return (
                    <tr key={user.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 text-sm text-gray-900">
                        {user.name}
                        {self && (
                          <span className="ml-2 inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-100 text-blue-800">
                            {t('admin.you')}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">{user.email}</td>
                      <td className="px-4 py-3 text-sm">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                            user.role === 'admin'
                              ? 'bg-purple-100 text-purple-800'
                              : 'bg-gray-100 text-gray-800'
                          }`}
                        >
                          {user.role === 'admin' ? t('admin.admin') : t('admin.user')}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                            user.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                          }`}
                        >
                          {user.is_active ? t('admin.active') : t('admin.inactive')}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">{formatDate(user.created_at)}</td>
                      <td className="px-4 py-3 text-right">
                        <div className="inline-flex items-center gap-1">
                          <button
                            onClick={() => openEdit(user)}
                            className="text-gray-400 hover:text-blue-600 transition-colors p-1 rounded"
                            title={t('admin.edit')}
                          >
                            <Pencil className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => { setResetEmail(user.email); setResetPassword(''); setResetError(''); }}
                            className="text-gray-400 hover:text-amber-600 transition-colors p-1 rounded"
                            title={t('admin.reset_password')}
                          >
                            <KeyRound className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => setDeleteUserEmail(user.email)}
                            disabled={self}
                            className="text-gray-400 hover:text-red-600 transition-colors p-1 rounded disabled:text-gray-200 disabled:cursor-not-allowed disabled:hover:text-gray-200"
                            title={self ? t('admin.delete_self_blocked') : t('admin.delete')}
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Create / Edit Modal */}
      {mode && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={closeForm}>
          <div className="bg-white rounded-xl shadow-lg p-6 max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 mb-4">
              {mode === 'create' ? t('admin.create_title') : t('admin.edit_title')}
            </h3>

            {formError && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
                <p className="text-sm text-red-700">{formError}</p>
              </div>
            )}

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t('admin.create_name')}
                </label>
                <input
                  type="text"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder={t('admin.create_name_placeholder')}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t('admin.create_email')}
                </label>
                <input
                  type="email"
                  value={formEmail}
                  onChange={(e) => setFormEmail(e.target.value)}
                  placeholder={t('admin.create_email_placeholder')}
                  disabled={mode === 'edit'}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                />
              </div>

              {mode === 'create' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {t('admin.create_password')}
                  </label>
                  <div className="relative">
                    <input
                      type={showFormPassword ? 'text' : 'password'}
                      value={formPassword}
                      onChange={(e) => setFormPassword(e.target.value)}
                      placeholder={t('admin.create_password_placeholder')}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => setShowFormPassword(s => !s)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600"
                      title={showFormPassword ? t('admin.hide_password') : t('admin.show_password')}
                    >
                      {showFormPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">{t('admin.password_hint')}</p>
                </div>
              )}

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t('admin.create_role')}
                </label>
                <select
                  value={formRole}
                  onChange={(e) => setFormRole(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                >
                  <option value="user">{t('admin.user')}</option>
                  <option value="admin">{t('admin.admin')}</option>
                </select>
              </div>

              {mode === 'edit' && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {t('admin.status')}
                  </label>
                  <select
                    value={formIsActive}
                    onChange={(e) => setFormIsActive(Number(e.target.value))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
                  >
                    <option value={1}>{t('admin.active')}</option>
                    <option value={0}>{t('admin.inactive')}</option>
                  </select>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={closeForm}
                disabled={isSubmitting}
                className="text-sm font-medium text-gray-600 hover:text-gray-900 px-4 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={submitForm}
                disabled={isSubmitting}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
              >
                {isSubmitting
                  ? t('admin.creating')
                  : mode === 'create' ? t('admin.create_submit') : t('admin.save')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reset password Modal */}
      {resetEmail && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={closeReset}>
          <div className="bg-white rounded-xl shadow-lg p-6 max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 mb-2">{t('admin.reset_password')}</h3>
            <p className="text-sm text-gray-500 mb-4">
              {t('admin.reset_for')}: <span className="font-medium text-gray-900">{resetEmail}</span>
            </p>

            {resetError && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4">
                <p className="text-sm text-red-700">{resetError}</p>
              </div>
            )}

            {/* Hidden username field so Chrome's "password change" heuristic
                anchors here instead of hijacking the page-level search box */}
            <input
              type="text"
              name="username"
              autoComplete="username"
              value={resetEmail ?? ''}
              readOnly
              tabIndex={-1}
              aria-hidden="true"
              className="absolute opacity-0 pointer-events-none w-0 h-0"
            />
            <div className="relative mb-4">
              <input
                type={showResetPassword ? 'text' : 'password'}
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                placeholder={t('admin.create_password_placeholder')}
                autoComplete="new-password"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button
                type="button"
                onClick={() => setShowResetPassword(s => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600"
              >
                {showResetPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <p className="text-xs text-gray-500 mb-4">{t('admin.password_hint')}</p>

            <div className="flex justify-end gap-3">
              <button
                onClick={closeReset}
                disabled={isResetting}
                className="text-sm font-medium text-gray-600 hover:text-gray-900 px-4 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={submitReset}
                disabled={isResetting || resetPassword.length < PASSWORD_MIN_LENGTH}
                className="bg-amber-600 hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
              >
                {isResetting ? t('admin.creating') : t('admin.reset_submit')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteUserEmail && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => !isDeleting && setDeleteUserEmail(null)}>
          <div className="bg-white rounded-xl shadow-lg p-6 max-w-sm w-full mx-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-semibold text-gray-900 mb-2">{t('admin.delete')}</h3>
            <p className="text-sm text-gray-500 mb-2">{t('admin.delete_confirm')}</p>
            <p className="text-sm text-gray-900 font-medium mb-6 break-all">{deleteUserEmail}</p>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setDeleteUserEmail(null)}
                disabled={isDeleting}
                className="text-sm font-medium text-gray-600 hover:text-gray-900 px-4 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleDeleteUser}
                disabled={isDeleting}
                className="bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
              >
                {isDeleting ? t('account.deleting') : t('admin.delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
