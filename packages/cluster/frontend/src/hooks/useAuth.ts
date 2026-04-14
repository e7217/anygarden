import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '@/lib/api';

interface User { id: string; email: string; is_admin: boolean; }

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchMe = useCallback(async () => {
    let token = localStorage.getItem('doorae_token');

    // No token — try dev-token auto-login (only works when DOORAE_DEV=1)
    if (!token) {
      try {
        const devResp = await fetch('/api/v1/auth/dev-token');
        if (devResp.ok) {
          const data = await devResp.json();
          localStorage.setItem('doorae_token', data.token);
          setUser(data.user);
          setLoading(false);
          return;
        }
      } catch { /* dev-token not available, normal flow */ }
      setLoading(false);
      return;
    }

    try {
      const resp = await apiFetch('/api/v1/auth/me');
      if (resp.ok) setUser(await resp.json());
      else { localStorage.removeItem('doorae_token'); setUser(null); }
    } catch { setUser(null); }
    setLoading(false);
  }, []);

  useEffect(() => { fetchMe(); }, [fetchMe]);

  const login = async (email: string, password: string) => {
    const resp = await apiFetch('/api/v1/auth/login', {
      method: 'POST', body: JSON.stringify({ email, password }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Login failed');
    const data = await resp.json();
    localStorage.setItem('doorae_token', data.token);
    setUser(data.user);
    return data;
  };

  const register = async (email: string, password: string) => {
    const resp = await apiFetch('/api/v1/auth/register', {
      method: 'POST', body: JSON.stringify({ email, password }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || 'Registration failed');
    const data = await resp.json();
    localStorage.setItem('doorae_token', data.token);
    await fetchMe();
    return data;
  };

  const logout = () => {
    localStorage.removeItem('doorae_token');
    setUser(null);
    window.location.href = '/login';
  };

  return { user, loading, login, register, logout };
}
