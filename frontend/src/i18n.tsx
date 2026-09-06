import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { english } from './messages';

type Locale = 'en' | 'zh-Hant';
type Translate = (message: string, values?: Record<string, string | number>) => string;
const LocaleContext = createContext<{ locale: Locale; setLocale: (locale: Locale) => void; t: Translate } | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(() => {
    try {
      return localStorage.getItem('echoscript.locale') === 'zh-Hant' ? 'zh-Hant' : 'en';
    } catch {
      return 'en';
    }
  });
  const t: Translate = (message, values = {}) => {
    const translated = locale === 'en' ? english[message] ?? message : message;
    return translated.replace(/\{(\w+)\}/g, (token, key) => String(values[key] ?? token));
  };
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = locale === 'en' ? 'EchoScript · Transcription workspace' : 'EchoScript · 轉錄工作台';
    try {
      localStorage.setItem('echoscript.locale', locale);
    } catch {
      // The switch still works when browser storage is unavailable.
    }
  }, [locale]);
  return <LocaleContext.Provider value={{ locale, setLocale, t }}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  const context = useContext(LocaleContext);
  if (!context) throw new Error('LocaleProvider is required');
  return context;
}
