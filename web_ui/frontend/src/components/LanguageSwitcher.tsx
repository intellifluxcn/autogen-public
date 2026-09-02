// Language switcher component for the user menu.

import { Languages, Check } from 'lucide-react';
import { useTranslation } from '../hooks/useTranslation';
import type { Language } from '../i18n';

interface LanguageSwitcherProps {
  onSelect?: () => void;
}

export function LanguageSwitcher({ onSelect }: LanguageSwitcherProps) {
  const { t, language, setLanguage, availableLanguages } = useTranslation();

  const handleSelectLanguage = (lang: Language) => {
    setLanguage(lang);
    onSelect?.();
  };

  return (
    <div className="py-1">
      <div className="px-3 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
        {t('user.language')}
      </div>
      {availableLanguages.map((lang) => (
        <button
          key={lang.code}
          onClick={() => handleSelectLanguage(lang.code)}
          className={[
            'w-full flex items-center justify-between px-4 py-2 text-sm transition-colors',
            language === lang.code
              ? 'text-blue-600 bg-blue-50 font-medium'
              : 'text-gray-700 hover:bg-gray-50',
          ].join(' ')}
        >
          <span>{lang.name}</span>
          {language === lang.code && (
            <Check className="w-4 h-4 text-blue-600" />
          )}
        </button>
      ))}
    </div>
  );
}

/**
 * Compact language selector button for inline use
 */
interface LanguageButtonProps {
  onClick?: () => void;
  className?: string;
}

export function LanguageButton({ onClick, className = '' }: LanguageButtonProps) {
  const { t, language } = useTranslation();

  return (
    <button
      onClick={onClick}
      className={[
        'flex items-center gap-2 px-3 py-1.5 rounded-lg',
        'text-sm font-medium transition-colors',
        'bg-gray-100 hover:bg-gray-200 text-gray-700',
        className,
      ].join(' ')}
      title={t('user.language')}
    >
      <Languages className="w-4 h-4" />
      <span>{language === 'zh' ? '中文' : 'English'}</span>
    </button>
  );
}
