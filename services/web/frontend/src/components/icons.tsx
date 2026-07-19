/** Iconos feather-style tomados de los paths del diseño. Sin dependencia externa. */
import type { ReactNode } from 'react';

interface IconProps {
  size?: number;
  sw?: number;
  className?: string;
  children: ReactNode;
}

function Icon({ size = 18, sw = 2.2, className, children }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

type P = { size?: number; sw?: number };

export const FootIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.2}>
    <path d="M4 14c0-4 3-8 8-8s8 4 8 8" />
    <path d="M7 20c0-2 2-4 5-4s5 2 5 4" />
  </Icon>
);
export const SearchIcon = (p: P) => (
  <Icon {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="M21 21l-4-4" />
  </Icon>
);
export const BellIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.3}>
    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
    <path d="M13.7 21a2 2 0 0 1-3.4 0" />
  </Icon>
);
export const FilterIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.4}>
    <path d="M3 5h18M6 12h12M10 19h4" />
  </Icon>
);
export const ArrowRightIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.4}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Icon>
);
export const ArrowLeftIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.4}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </Icon>
);
export const CheckIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.6}>
    <path d="M20 6L9 17l-5-5" />
  </Icon>
);
export const AlertIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.4}>
    <path d="M12 3L2 20h20L12 3z" />
    <path d="M12 9v5M12 17.2v.1" />
  </Icon>
);
export const CloseIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.4}>
    <path d="M6 6l12 12M18 6L6 18" />
  </Icon>
);
export const ExternalIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.3}>
    <path d="M7 17L17 7M9 7h8v8" />
  </Icon>
);
export const ZapIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.4}>
    <path d="M13 2L3 14h7l-1 8 10-12h-7z" />
  </Icon>
);
export const SunIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.2}>
    <circle cx="12" cy="12" r="4.5" />
    <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19" />
  </Icon>
);
export const MoonIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.2}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
  </Icon>
);
export const HomeIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.2}>
    <path d="M3 11l9-8 9 8" />
    <path d="M5 10v10h14V10" />
  </Icon>
);
export const GridIcon = (p: P) => (
  <Icon {...p} sw={p.sw ?? 2.2}>
    <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" />
  </Icon>
);
