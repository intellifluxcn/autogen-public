// React hook for using i18n translations in components.

import { useState, useEffect, useCallback } from 'react';
import { 
  t, 
  getLanguage, 
  setLanguage, 
  onLanguageChange,
  Language,
  getAvailableLanguages 
} from '../i18n';

/**
 * Hook to use translations and language switching in components
 * 
 * @example
 * ```tsx
 * function MyComponent() {
 *   const { t, language, setLanguage } = useTranslation();
 *   
 *   return (
 *     <div>
 *       <h1>{t('home.title')}</h1>
 *       <button onClick={() => setLanguage(language === 'en' ? 'zh' : 'en')}>
 *         Switch Language
 *       </button>
 *     </div>
 *   );
 * }
 * ```
 */
export function useTranslation() {
  const [language, setLanguageState] = useState<Language>(getLanguage());

  useEffect(() => {
    // Subscribe to language changes
    const unsubscribe = onLanguageChange((newLang) => {
      setLanguageState(newLang);
    });

    return unsubscribe;
  }, []);

  const changeLanguage = useCallback((lang: Language) => {
    setLanguage(lang);
  }, []);

  const availableLanguages = getAvailableLanguages();

  return {
    /** Translation function */
    t,
    /** Current language code ('en' | 'zh') */
    language,
    /** Function to change language */
    setLanguage: changeLanguage,
    /** Available languages list */
    availableLanguages,
    /** Whether current language is Chinese */
    isChinese: language === 'zh',
    /** Whether current language is English */
    isEnglish: language === 'en',
  };
}

// Note: For class components, use the useTranslation hook in a functional wrapper component instead of withTranslation HOC.
