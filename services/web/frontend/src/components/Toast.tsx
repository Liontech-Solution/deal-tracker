import { createContext, useCallback, useContext, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { CheckIcon } from './icons';

const ToastContext = createContext<(message: string) => void>(() => {});

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  const show = useCallback((msg: string) => {
    setMessage(msg);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMessage(null), 2800);
  }, []);

  return (
    <ToastContext.Provider value={show}>
      {children}
      {message && (
        <div
          role="status"
          style={{
            position: 'fixed',
            bottom: 22,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 90,
            background: 'var(--ink-900)',
            color: '#fff',
            borderRadius: 'var(--r-pill)',
            padding: '13px 20px 13px 16px',
            boxShadow: 'var(--shadow-3)',
            display: 'flex',
            alignItems: 'center',
            gap: 11,
            fontWeight: 700,
            fontSize: 14,
            animation: 'dt-toast .28s cubic-bezier(.16,1,.3,1)',
            maxWidth: '90vw',
          }}
        >
          <span
            style={{
              width: 26,
              height: 26,
              borderRadius: '50%',
              background: 'var(--good)',
              color: '#fff',
              display: 'grid',
              placeItems: 'center',
              flex: 'none',
            }}
          >
            <CheckIcon size={15} sw={3} />
          </span>
          {message}
        </div>
      )}
    </ToastContext.Provider>
  );
}
