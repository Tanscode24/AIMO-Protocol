"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

export type Me = {
  id: string;
  name: string;
  email: string;
  role: string;
  kind: string;
  organization_id: string;
  organization_name?: string;
  read_scope: "OWN" | "ORG";
  permissions: string[];
};

type AuthCtx = {
  user: Me | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
};

const Ctx = createContext<AuthCtx>({ user: null, ready: false, login: async () => {}, logout: async () => {}, can: () => false });

// UI permission checks only hide controls; every action is authorised again by the backend.
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api<Me>("/api/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    await api("/api/auth/login", { method: "POST", json: { email, password } });
    setUser(await api<Me>("/api/auth/me"));
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
      window.location.href = "/login";
    }
  }, []);

  const can = useCallback((p: string) => !!user?.permissions?.includes(p), [user]);

  return <Ctx.Provider value={{ user, ready, login, logout, can }}>{children}</Ctx.Provider>;
}

export function useAuth() {
  return useContext(Ctx);
}
