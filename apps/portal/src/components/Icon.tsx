import type { ReactNode, SVGProps } from "react";

export type IconName =
  | "workspace"
  | "plans"
  | "resources"
  | "operations"
  | "estimates"
  | "docs"
  | "connections"
  | "account"
  | "operator"
  | "menu"
  | "close"
  | "chevronDown"
  | "chevronRight"
  | "arrowLeft"
  | "external"
  | "refresh"
  | "search"
  | "alert"
  | "check"
  | "info"
  | "copy"
  | "logout"
  | "send"
  | "lock"
  | "plus"
  | "monitor"
  | "clock"
  | "save";

const PATHS: Record<IconName, ReactNode> = {
  workspace: (
    <>
      <path d="M4 5.5h16v10H9l-5 4v-14Z" />
      <path d="M8 9h8M8 12h5" />
    </>
  ),
  plans: (
    <>
      <path d="M6 3.5h9l3 3V20H6V3.5Z" />
      <path d="M15 3.5V7h3M9 11h6M9 14h6M9 17h3" />
    </>
  ),
  resources: (
    <>
      <rect x="3.5" y="4.5" width="17" height="6" rx="1.5" />
      <rect x="3.5" y="13.5" width="17" height="6" rx="1.5" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </>
  ),
  operations: (
    <>
      <path d="M3 12h4l2.5-7 4 14 2.5-7h5" />
    </>
  ),
  estimates: (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M8.5 7h7M8.5 11h2M13 11h2.5M8.5 15h2M13 15h2.5" />
    </>
  ),
  docs: (
    <>
      <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v16.5H6.5A2.5 2.5 0 0 0 4 22V5.5Z" />
      <path d="M4 5.5A2.5 2.5 0 0 0 6.5 8H20M8 12h8M8 15h5" />
    </>
  ),
  connections: (
    <>
      <path d="M9 15 15 9M7.5 12.5l-2 2a3.5 3.5 0 0 0 5 5l2-2M16.5 11.5l2-2a3.5 3.5 0 0 0-5-5l-2 2" />
    </>
  ),
  account: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4.5 21c.8-4.2 3.8-6.5 7.5-6.5s6.7 2.3 7.5 6.5" />
    </>
  ),
  operator: (
    <>
      <path d="M12 3 20 6v6c0 4.6-3.2 7.8-8 9-4.8-1.2-8-4.4-8-9V6l8-3Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronRight: <path d="m9 6 6 6-6 6" />,
  arrowLeft: <path d="M20 12H4m6-6-6 6 6 6" />,
  external: (
    <>
      <path d="M14 4h6v6M20 4l-9 9" />
      <path d="M19 13v6H5V5h6" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 11a8 8 0 0 0-14.8-4M4 4v4h4" />
      <path d="M4 13a8 8 0 0 0 14.8 4M20 20v-4h-4" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  alert: (
    <>
      <path d="M12 3 22 20H2L12 3Z" />
      <path d="M12 9v5M12 17.5h.01" />
    </>
  ),
  check: <path d="m4 12 5 5L20 6" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6M12 7.5h.01" />
    </>
  ),
  copy: (
    <>
      <rect x="8" y="8" width="12" height="12" rx="2" />
      <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
    </>
  ),
  logout: (
    <>
      <path d="M10 4H5v16h5M14 8l4 4-4 4M18 12H9" />
    </>
  ),
  send: <path d="M3 11.5 21 4l-7.5 18-2.5-7.5L3 11.5Z" />,
  lock: (
    <>
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  monitor: (
    <>
      <rect x="3" y="4" width="18" height="12" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  save: (
    <>
      <path d="M5 3h11l3 3v15H5V3Z" />
      <path d="M8 3v6h8V3M8 21v-7h8v7" />
    </>
  ),
};

interface IconProps extends Omit<SVGProps<SVGSVGElement>, "name"> {
  name: IconName;
  size?: number;
  title?: string;
}

export function Icon({ name, size = 20, title, ...rest }: IconProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      {...rest}
    >
      {title ? <title>{title}</title> : null}
      {PATHS[name]}
    </svg>
  );
}
