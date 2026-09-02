// Translate raw backend/network errors into localized UI messages.

type TranslateFn = (key: string) => string;

export function translateErrorMessage(
  message: string | undefined | null,
  t: TranslateFn,
  fallbackKey: string = 'common.error',
): string {
  const text = String(message || '').trim();
  if (!text) return t(fallbackKey);

  const lower = text.toLowerCase();

  if (lower.includes('cannot delete')) return t('admin.cannot_delete_admin');
  if (lower.includes('authentication expired')) return t('error.auth');
  if (lower.includes('failed to connect')) return t('error.network');
  if (lower.includes('api request failed')) return t('error.server');
  if (lower.includes('failed to fetch')) return t('error.server');
  if (lower.includes('project not found')) return t('detail.not_found');
  if (lower.includes('invalid credentials') || lower.includes('invalid email or password')) {
    return t('login.error');
  }
  if (lower.includes('account is disabled')) return t('login.error_disabled');
  if (lower.includes('invalid or expired reset token')) return t('reset.error');
  if (lower.includes('please enter at least one recipient email address')) {
    return t('email.error_no_recipients');
  }
  if (lower.includes('failed to send email')) return t('email.send_failed');

  return text;
}
