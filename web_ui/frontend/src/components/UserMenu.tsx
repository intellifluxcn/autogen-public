// Dropdown menu triggered by the user avatar with account, language, admin, and logout options.

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ChevronLeft,
  CircleUser,
  Languages,
  LogOut,
  Settings,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { useTranslation } from '../hooks/useTranslation';
import { LanguageSwitcher } from './LanguageSwitcher';

interface UserMenuProps {
  isOpen: boolean;
  onClose: () => void;
}

export function UserMenu({ isOpen, onClose }: UserMenuProps) {
  const { email, logout, isAdmin } = useAuth();
  const navigate = useNavigate();
  const menuRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();

  const [visible, setVisible] = useState(false);
  const [showLanguageSubmenu, setShowLanguageSubmenu] = useState(false);

  useEffect(() => {
    if (isOpen) {
      requestAnimationFrame(() => setVisible(true));
    } else {
      setVisible(false);
      setShowLanguageSubmenu(false);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSelectLanguage = () => {
    setShowLanguageSubmenu(false);
  };

  return (
    <div
      ref={menuRef}
      className={[
        'absolute right-0 top-full mt-2 z-50',
        'w-72 bg-white rounded-xl shadow-lg border border-gray-200',
        'transition-all duration-150 ease-out origin-top-right',
        visible ? 'scale-100 opacity-100' : 'scale-95 opacity-0',
      ].join(' ')}
    >
      <div className="flex items-center gap-3 px-4 py-3">
        <CircleUser className="w-9 h-9 text-gray-500 shrink-0" />
        <div className="min-w-0">
          <p className="text-gray-900 font-semibold truncate">{email}</p>
          <p className="text-xs text-gray-500">{t('user.account')}</p>
        </div>
      </div>

      <div className="border-t border-gray-200" />

      <button
        onClick={() => { onClose(); navigate('/account'); }}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
      >
        <CircleUser className="w-4 h-4 text-gray-500" />
        <span>{t('user.account')}</span>
      </button>

      {isAdmin && (
        <button
          onClick={() => { onClose(); navigate('/admin'); }}
          className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <Settings className="w-4 h-4 text-gray-500" />
          <span>{t('admin.title')}</span>
        </button>
      )}

      <div className="relative">
        <button
          onClick={() => setShowLanguageSubmenu(p => !p)}
          className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <ChevronLeft className="w-4 h-4 text-gray-400" />
          <Languages className="w-4 h-4 text-gray-500" />
          <span className="flex-1 text-left">{t('user.language')}</span>
        </button>

        {showLanguageSubmenu && (
          <div
            className={[
              // xs: drop below the language row, fill parent menu width
              'absolute top-full left-0 mt-1 w-full',
              // sm+: original side-out submenu to the left of the menu
              'sm:top-0 sm:left-auto sm:right-full sm:mt-0 sm:mr-2 sm:w-36',
              'bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-50',
            ].join(' ')}
          >
            <LanguageSwitcher onSelect={handleSelectLanguage} />
          </div>
        )}
      </div>

      <div className="border-t border-gray-200" />

      <button
        onClick={() => {
          onClose();
          logout();
        }}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors rounded-b-xl"
      >
        <LogOut className="w-4 h-4 text-gray-500" />
        <span>{t('user.logout')}</span>
      </button>
    </div>
  );
}
