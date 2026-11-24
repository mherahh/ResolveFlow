"use client";
import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import {
  AuthUser,
  authLogin,
  authLogout,
  authMe,
  authRegister,
  demoSwitchAccount,
  getAuthConfig,
  getStoredToken,
  setStoredToken,
  setUnauthorizedListener,
} from "@/lib/api";

type AuthContextValue = {
  user: AuthUser | null;
  isDemo: boolean;
  demoModeEnabled: boolean;
  loading: boolean;
  sessionNotice: string;
  clearNotice: () => void;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchDemo: (accountId: string) => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isDemo, setIsDemo] = useState(false);
  const [demoModeEnabled, setDemoModeEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sessionNotice, setSessionNotice] = useState("");

  const handleUnauthorized = useCallback(() => {
    setStoredToken(null);
    setUser((prev) => {
      if (prev) {
        setSessionNotice("Your session expired or was revoked. Please sign in again.");
      }
      return null;
    });
    setIsDemo(false);
  }, []);

  useEffect(() => {
    setUnauthorizedListener(handleUnauthorized);
    return () => setUnauthorizedListener(null);
  }, [handleUnauthorized]);

  useEffect(() => {
    let active = true;
    async function bootstrap() {
      try {
        const cfg = await getAuthConfig();
        if (active) setDemoModeEnabled(cfg.demo_mode_enabled);
      } catch {
        // backend may be starting
      }
      const token = getStoredToken();
      if (token) {
        try {
          const res = await authMe();
          if (active) {
            setUser(res.user);
            setIsDemo(res.is_demo);
            setDemoModeEnabled(res.demo_mode_enabled);
          }
        } catch {
          if (active) {
            setStoredToken(null);
            setUser(null);
          }
        }
      }
      if (active) setLoading(false);
    }
    bootstrap();
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await authLogin(email, password);
    setStoredToken(res.token);
    setUser(res.user);
    setIsDemo(false);
    setSessionNotice("");
  }, []);

  const register = useCallback(async (name: string, email: string, password: string) => {
    const res = await authRegister(name, email, password);
    setStoredToken(res.token);
    setUser(res.user);
    setIsDemo(false);
    setSessionNotice("");
  }, []);

  const logout = useCallback(async () => {
    try {
      await authLogout();
    } catch {
      // clear local token regardless
    }
    setStoredToken(null);
    setUser(null);
    setIsDemo(false);
    setSessionNotice("You have been signed out.");
  }, []);

  const switchDemo = useCallback(async (accountId: string) => {
    const res = await demoSwitchAccount(accountId);
    setStoredToken(res.token);
    setUser(res.user);
    setIsDemo(true);
    setSessionNotice("");
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isDemo,
        demoModeEnabled,
        loading,
        sessionNotice,
        clearNotice: () => setSessionNotice(""),
        login,
        register,
        logout,
        switchDemo,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
