import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

/**
 * Node 26 exposes an experimental global localStorage that is undefined in
 * this jsdom environment and can shadow jsdom's own storage. Install a small
 * in-memory Storage so the app's real locale-persistence path can be tested.
 */
function installTestStorage(): void {
  let existing: Storage | undefined;
  try {
    existing = window.localStorage;
  } catch {
    existing = undefined;
  }
  if (existing && typeof existing.getItem === "function") return;

  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear() {
      values.clear();
    },
    getItem(key: string) {
      return values.has(key) ? (values.get(key) as string) : null;
    },
    key(index: number) {
      return [...values.keys()][index] ?? null;
    },
    removeItem(key: string) {
      values.delete(key);
    },
    setItem(key: string, value: string) {
      values.set(key, String(value));
    },
  };
  Object.defineProperty(window, "localStorage", { value: storage, configurable: true });
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true });
}

installTestStorage();

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  document.title = "";
  delete document.documentElement.dataset.brand;
  delete document.documentElement.dataset.environment;
  document.documentElement.style.removeProperty("--brand-primary");
  document.documentElement.style.removeProperty("--brand-accent");
  document.documentElement.style.removeProperty("--brand-contrast");
});
