// Account settings page with profile info, password change, linked paywall accounts, and delete account.

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Eye, EyeOff, X, AlertTriangle, CheckCircle } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useTranslation } from '../hooks/useTranslation';
import { apiClient } from '../utils/api';
import { translateErrorMessage } from '../utils/error';

const PAYWALL_SITES = [
  'NCBI/PubMed',
  'Nature/Springer',
  'Elsevier/ScienceDirect',
  'Wiley Online Library',
  'IEEE Xplore',
  'JSTOR',
  'Oxford Academic',
  'Taylor & Francis',
  'AAAS/Science',
  'Cell Press',
];

interface LinkedAccount {
  id: number;
  site: string;
  credential: string;
  created_at?: string;
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-xl font-semibold text-gray-900 mb-3">{title}</h2>
      <div className="border-t border-gray-200" />
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-b-0">
      <span className="text-sm font-medium text-gray-700">{label}</span>
      <span className="text-sm text-gray-400">{value}</span>
    </div>
  );
}

export function AccountPage() {
  const navigate = useNavigate();
  const { email, name } = useAuth();
  const { t } = useTranslation();

  const [selectedSite, setSelectedSite] = useState('');
  const [linkedAccounts, setLinkedAccounts] = useState<LinkedAccount[]>([]);
  const [credential, setCredential] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [isLoading, setIsLoading] = useState(true);

  // Change password state
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [changePasswordError, setChangePasswordError] = useState('');
  const [changePasswordSuccess, setChangePasswordSuccess] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);

  useEffect(() => {
    apiClient.get<LinkedAccount[]>('/linked-accounts')
      .then(data => setLinkedAccounts(data))
      .catch(() => {})
      .finally(() => setIsLoading(false));
  }, []);

  const handleAddAccount = async () => {
    if (!selectedSite || !credential || !password) return;
    setIsSaving(true);
    setFormError('');
    try {
      const account = await apiClient.post<LinkedAccount>('/linked-accounts', {
        site: selectedSite,
        credential,
        password,
      });
      setLinkedAccounts(prev => [account, ...prev]);
      setSelectedSite('');
      setCredential('');
      setPassword('');
      setShowPassword(false);
    } catch (err: any) {
      const msg: string = err?.message ?? '';
      if (msg.includes('409')) {
        setFormError(t('account.account_exists') + selectedSite + t('account.already_exists'));
      } else if (msg.includes('500')) {
        setFormError(t('account.encryption_error'));
      } else {
        setFormError(translateErrorMessage(msg, t, 'account.save_failed'));
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleRemoveAccount = async (accountId: number) => {
    try {
      await apiClient.delete(`/linked-accounts/${accountId}`);
      setLinkedAccounts(prev => prev.filter(a => a.id !== accountId));
    } catch {
    }
  };

  const handleChangePassword = async () => {
    setChangePasswordError('');
    setChangePasswordSuccess(false);

    if (newPassword.length < 8) {
      setChangePasswordError(t('error.password_length'));
      return;
    }

    if (newPassword !== confirmNewPassword) {
      setChangePasswordError(t('account.password_mismatch'));
      return;
    }

    setIsChangingPassword(true);
    try {
      await apiClient.put('/auth/password', {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setChangePasswordSuccess(true);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmNewPassword('');
      setTimeout(() => {
        setShowChangePassword(false);
        setChangePasswordSuccess(false);
      }, 2000);
    } catch (err: any) {
      const msg = String(err?.response?.data?.detail || err?.message || '');
      if (msg.toLowerCase().includes('incorrect')) {
        setChangePasswordError(t('account.wrong_current_password'));
      } else {
        setChangePasswordError(translateErrorMessage(msg, t, 'account.password_error'));
      }
    } finally {
      setIsChangingPassword(false);
    }
  };

  const maskCredential = (cred: string) => {
    if (cred.includes('@')) {
      const [local, domain] = cred.split('@');
      return `${local.slice(0, 2)}***@${domain}`;
    }
    return `${cred.slice(0, 3)}***`;
  };

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

      <div className="max-w-2xl mx-auto px-6 py-10">
        <section>
          <SectionHeader title={t('account.title')} />
          <div className="bg-white rounded-xl border border-gray-200 px-5">
            <InfoRow label={t('account.full_name')} value={name || t('account.user')} />
            <InfoRow label={t('account.email')} value={email || ''} />
          </div>
        </section>

        <section className="mt-10">
          <SectionHeader title={t('account.change_password')} />
          <div className="bg-white rounded-xl border border-gray-200 px-5 py-4">
            {!showChangePassword ? (
              <button
                onClick={() => setShowChangePassword(true)}
                className="text-blue-600 hover:text-blue-700 text-sm font-medium"
              >
                {t('account.change_password_btn')}
              </button>
            ) : (
              <div className="space-y-4">
                {changePasswordSuccess ? (
                  <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex items-center gap-2">
                    <CheckCircle className="w-5 h-5 text-green-600" />
                    <p className="text-sm text-green-700">{t('account.password_changed')}</p>
                  </div>
                ) : (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        {t('account.current_password')}
                      </label>
                      <input
                        type="password"
                        value={currentPassword}
                        onChange={(e) => setCurrentPassword(e.target.value)}
                        placeholder={t('account.current_password_placeholder')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        {t('account.new_password')}
                      </label>
                      <div className="relative">
                        <input
                          type={showNewPassword ? 'text' : 'password'}
                          value={newPassword}
                          onChange={(e) => setNewPassword(e.target.value)}
                          placeholder={t('account.new_password_placeholder')}
                          className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                        <button
                          type="button"
                          onClick={() => setShowNewPassword((v) => !v)}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                        >
                          {showNewPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        {t('account.confirm_password')}
                      </label>
                      <input
                        type="password"
                        value={confirmNewPassword}
                        onChange={(e) => setConfirmNewPassword(e.target.value)}
                        placeholder={t('account.confirm_password_placeholder')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                      />
                    </div>

                    {changePasswordError && (
                      <div className="flex items-center gap-2 text-red-600 text-sm">
                        <AlertTriangle className="w-4 h-4" />
                        {changePasswordError}
                      </div>
                    )}

                    <div className="flex gap-3 pt-2">
                      <button
                        onClick={() => {
                          setShowChangePassword(false);
                          setChangePasswordError('');
                          setCurrentPassword('');
                          setNewPassword('');
                          setConfirmNewPassword('');
                        }}
                        className="text-sm font-medium text-gray-600 hover:text-gray-900 px-4 py-2 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors"
                      >
                        {t('common.cancel')}
                      </button>
                      <button
                        onClick={handleChangePassword}
                        disabled={isChangingPassword || !currentPassword || !newPassword || !confirmNewPassword}
                        className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
                      >
                        {isChangingPassword ? t('account.changing') : t('account.change_password_btn')}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        </section>

        <section className="mt-10">
          <SectionHeader title={t('account.linked_accounts')} />

          <div className="bg-white rounded-xl border border-gray-200 px-5 py-4 space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">
                {t('account.add_paywall')}
              </label>
              <select
                value={selectedSite}
                onChange={e => { setSelectedSite(e.target.value); setFormError(''); }}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white text-gray-700"
              >
                <option value="">{t('account.select_site')}</option>
                {PAYWALL_SITES.map(site => (
                  <option key={site} value={site}>{site}</option>
                ))}
              </select>
            </div>

            {selectedSite && (
              <div className="space-y-3 pt-1">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    {t('account.email_or_member')}
                    <span className="text-red-500 ml-0.5">*</span>
                  </label>
                  <input
                    type="text"
                    value={credential}
                    onChange={e => setCredential(e.target.value)}
                    placeholder="you@example.com"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Password
                    <span className="text-red-500 ml-0.5">*</span>
                  </label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      placeholder="••••••••"
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(p => !p)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition-colors"
                      aria-label={showPassword ? t('login.hide_password') : t('login.show_password')}
                    >
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {formError && (
                  <p className="text-sm text-red-600">{formError}</p>
                )}

                <div className="flex justify-end pt-1">
                  <button
                    onClick={handleAddAccount}
                    disabled={!credential || !password || isSaving}
                    className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
                  >
                    {isSaving ? t('account.saving') : t('account.add_account')}
                  </button>
                </div>
              </div>
            )}

            {isLoading ? (
              <p className="text-sm text-gray-400 py-2">{t('account.loading')}</p>
            ) : linkedAccounts.length > 0 ? (
              <div className="border-t border-gray-100 pt-3 space-y-2">
                {linkedAccounts.map(acct => (
                  <div
                    key={acct.id}
                    className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded-lg"
                  >
                    <div className="flex flex-col">
                      <span className="text-sm font-medium text-gray-800">{acct.site}</span>
                      <span className="text-xs text-gray-400">{maskCredential(acct.credential)}</span>
                    </div>
                    <button
                      onClick={() => handleRemoveAccount(acct.id)}
                      className="text-gray-400 hover:text-red-500 transition-colors p-1 rounded"
                      aria-label={`${t('account.remove')} ${acct.site}`}
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
