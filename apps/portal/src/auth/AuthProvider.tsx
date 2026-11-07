import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/endpoints";
import { ApiError, setCsrfToken, setUnauthorizedHandler } from "../api/client";
import { isAbortError } from "../lib/errors";
import { safeHttpUrl, safeSameOriginPath } from "../lib/safe";
import {
  AuthContext,
  type AuthContextValue,
  type AuthStatus,
} from "./context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<AuthContextValue["session"]>(null);
  const [sessionError, setSessionError] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const refresh = useCallback(async () => {
    setStatus((previous) => (previous === "authenticated" ? previous : "loading"));
    try {
      const info = await api.getSession();
      setCsrfToken(info.csrf_token);
      setSession(info);
      setSessionError(false);
      setStatus("authenticated");
    } catch (error) {
      setCsrfToken(null);
      setSession(null);
      setStatus("anonymous");
      if (error instanceof ApiError && error.status === 401) {
        setSessionError(false);
      } else if (!isAbortError(error)) {
        setSessionError(true);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Any authenticated request that receives a 401 returns the user to login.
  useEffect(() => {
    const current =
      safeSameOriginPath(`${location.pathname}${location.search}`) ?? "/";
    setUnauthorizedHandler(() => {
      setCsrfToken(null);
      setSession(null);
      setStatus("anonymous");
      if (location.pathname !== "/login") {
        navigate("/login?reason=expired", { replace: true, state: { from: current } });
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate, location.pathname, location.search]);

  const loginDemo = useCallback(async (subject: string) => {
    await api.demoLogin(subject);
    const info = await api.getSession();
    setCsrfToken(info.csrf_token);
    setSession(info);
    setSessionError(false);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async () => {
    const result = await api.logout();
    setCsrfToken(null);
    setSession(null);
    setStatus("anonymous");
    const external = result.logout_url ? safeHttpUrl(result.logout_url) : null;
    return external;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, session, sessionError, refresh, loginDemo, logout }),
    [status, session, sessionError, refresh, loginDemo, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
