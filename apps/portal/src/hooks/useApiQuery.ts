import { useCallback, useEffect, useRef, useState } from "react";
import { isAbortError } from "../lib/errors";

export type QueryStatus = "idle" | "loading" | "success" | "error";

export interface ApiQueryState<T> {
  status: QueryStatus;
  data: T | null;
  error: unknown;
}

export interface ApiQueryResult<T> extends ApiQueryState<T> {
  reload: () => void;
  /** True only for the first load, so refreshes can keep the current content visible. */
  initialLoading: boolean;
}

/**
 * Run an abortable API query and re-run it when `deps` change.
 *
 * The loader receives an AbortSignal; aborted requests are ignored. The
 * previous data remains visible while a manual reload is in flight.
 */
export function useApiQuery<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean } = {},
): ApiQueryResult<T> {
  const enabled = options.enabled ?? true;
  const [state, setState] = useState<ApiQueryState<T>>({
    status: enabled ? "loading" : "idle",
    data: null,
    error: null,
  });
  const [nonce, setNonce] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const depsKey = JSON.stringify(deps);
  const previousDepsKey = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setState({ status: "idle", data: null, error: null });
      return undefined;
    }
    const controller = new AbortController();
    let active = true;
    const keyChanged = previousDepsKey.current !== depsKey;
    previousDepsKey.current = depsKey;
    setState((previous) => ({
      status: "loading",
      // A different query key must not show another resource's stale data.
      // A manual reload keeps the previous result visible while revalidating.
      data: keyChanged ? null : previous.data,
      error: null,
    }));

    loaderRef
      .current(controller.signal)
      .then((data) => {
        if (!active || controller.signal.aborted) return;
        setState({ status: "success", data, error: null });
      })
      .catch((error: unknown) => {
        if (!active || controller.signal.aborted || isAbortError(error)) return;
        setState({ status: "error", data: null, error });
      });

    return () => {
      active = false;
      controller.abort();
    };
    // The caller provides the complete dependency list for its query.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return {
    ...state,
    reload,
    initialLoading: state.status === "loading" && state.data === null,
  };
}
