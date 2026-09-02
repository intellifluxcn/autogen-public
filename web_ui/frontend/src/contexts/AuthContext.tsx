// Authentication context providing login, register, logout, and token management.

import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { socketClient } from '../utils/socket';

interface AuthState {
  isAuthenticated: boolean;
  email: string | null;
  name: string | null;
  token: string | null;
  isAdmin: boolean;
}

interface AuthContextType extends AuthState {
  login: (email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  register: (name: string, email: string, password: string) => Promise<{ success: boolean; error?: string }>;
  logout: () => void;
  isLoading: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

const AUTH_TOKEN_KEY = 'auth_token';
const AUTH_EMAIL_KEY = 'auth_email';
const AUTH_NAME_KEY = 'auth_name';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [authState, setAuthState] = useState<AuthState>({
    isAuthenticated: false,
    email: null,
    name: null,
    token: null,
    isAdmin: false,
  });

  const checkIsAdmin = useCallback(async (token: string) => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const res = await fetch(`${apiUrl}/api/auth/admin-check`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setAuthState(prev => ({ ...prev, isAdmin: data.is_admin }));
      }
    } catch {
      // Ignore admin check errors
    }
  }, []);

  useEffect(() => {
    const token = sessionStorage.getItem(AUTH_TOKEN_KEY);
    const email = sessionStorage.getItem(AUTH_EMAIL_KEY);
    const storedName = sessionStorage.getItem(AUTH_NAME_KEY);

    if (!token || !email) {
      setIsLoading(false);
      return;
    }

    const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
    const verifyCtl = new AbortController();
    const verifyTimer = window.setTimeout(() => verifyCtl.abort(), 12_000);

    const finishLoading = () => {
      window.clearTimeout(verifyTimer);
      setIsLoading(false);
    };

    fetch(`${apiUrl}/api/auth/verify`, {
      headers: { 'Authorization': `Bearer ${token}` },
      signal: verifyCtl.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          sessionStorage.removeItem(AUTH_TOKEN_KEY);
          sessionStorage.removeItem(AUTH_EMAIL_KEY);
          sessionStorage.removeItem(AUTH_NAME_KEY);
          return;
        }

        let name = storedName;
        if (!storedName) {
          const meCtl = new AbortController();
          const meTimer = window.setTimeout(() => meCtl.abort(), 8_000);
          try {
            const meRes = await fetch(`${apiUrl}/api/auth/me`, {
              headers: { 'Authorization': `Bearer ${token}` },
              signal: meCtl.signal,
            });
            if (meRes.ok) {
              const profile = await meRes.json();
              sessionStorage.setItem(AUTH_NAME_KEY, profile.name);
              name = profile.name;
            }
          } catch {
            // offline, timeout, or /me failed — still finish bootstrap
          } finally {
            window.clearTimeout(meTimer);
          }
        }

        setAuthState({
          isAuthenticated: true,
          email,
          name,
          token,
          isAdmin: false,
        });
        checkIsAdmin(token);
      })
      .catch(() => {
        setAuthState({
          isAuthenticated: true,
          email,
          name: storedName,
          token,
          isAdmin: false,
        });
      })
      .finally(finishLoading);
  }, [checkIsAdmin]);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const response = await fetch(`${apiUrl}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        return { success: false, error: data.detail || 'Invalid credentials' };
      }

      const data = await response.json();

      sessionStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
      sessionStorage.setItem(AUTH_EMAIL_KEY, data.email);
      if (data.name) sessionStorage.setItem(AUTH_NAME_KEY, data.name);

      setAuthState({
        isAuthenticated: true,
        email: data.email,
        name: data.name ?? null,
        token: data.access_token,
        isAdmin: false,
      });

      // Check admin status
      checkIsAdmin(data.access_token);

      socketClient.reconnect();

      return { success: true };
    } catch (error) {
      return { success: false, error: 'Failed to connect to server' };
    }
  }, [checkIsAdmin]);

  const register = useCallback(async (name: string, email: string, password: string) => {
    try {
      const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
      const response = await fetch(`${apiUrl}/api/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, email, password }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        return { success: false, error: data.detail || 'Registration failed' };
      }

      const data = await response.json();

      sessionStorage.setItem(AUTH_TOKEN_KEY, data.access_token);
      sessionStorage.setItem(AUTH_EMAIL_KEY, data.email);
      if (data.name) sessionStorage.setItem(AUTH_NAME_KEY, data.name);

      setAuthState({
        isAuthenticated: true,
        email: data.email,
        name: data.name ?? null,
        token: data.access_token,
        isAdmin: false,
      });

      // Check admin status
      checkIsAdmin(data.access_token);

      socketClient.reconnect();

      return { success: true };
    } catch (error) {
      return { success: false, error: 'Failed to connect to server' };
    }
  }, [checkIsAdmin]);

  const logout = useCallback(() => {
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
    sessionStorage.removeItem(AUTH_EMAIL_KEY);
    sessionStorage.removeItem(AUTH_NAME_KEY);
    setAuthState({ isAuthenticated: false, email: null, name: null, token: null, isAdmin: false });
    socketClient.disconnect();
  }, []);

  return (
    <AuthContext.Provider value={{ ...authState, login, register, logout, isLoading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
