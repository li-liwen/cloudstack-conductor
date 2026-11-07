import { createContext, useContext } from "react";
import type { SessionInfo } from "../api/types";

export type AuthStatus = "loading" | "authenticated" | "anonymous";

export interface AuthContextValue {
  status: AuthStatus;
  session: SessionInfo | null;
  /** True when session verification failed for a reason other than 401. */
  sessionError: boolean;
  refresh: () => Promise<void>;
  loginDemo: (subject: string) => Promise<void>;
  logout: () => Promise<string | null>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside an AuthProvider");
  return value;
}

export function isOperator(session: SessionInfo | null): boolean {
  return Boolean(session?.principal.roles?.includes("operator"));
}
