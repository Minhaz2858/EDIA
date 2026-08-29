import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';

export type Language = 'zh' | 'en';

const STORAGE_KEY = 'edia_ui_language';

interface LanguageContextValue {
    language: Language;
    locale: string;
    setLanguage: (language: Language) => void;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

function loadInitialLanguage(): Language {
    try {
        const raw = window.localStorage.getItem(STORAGE_KEY);
        return raw === 'en' ? 'en' : 'zh';
    } catch {
        return 'zh';
    }
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
    const [language, setLanguageState] = useState<Language>(() => loadInitialLanguage());

    useEffect(() => {
        try {
            window.localStorage.setItem(STORAGE_KEY, language);
        } catch {
            // Ignore storage failures.
        }
        document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
    }, [language]);

    const value = useMemo<LanguageContextValue>(
        () => ({
            language,
            locale: language === 'en' ? 'en-US' : 'zh-CN',
            setLanguage: setLanguageState,
        }),
        [language],
    );

    return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage(): LanguageContextValue {
    const context = useContext(LanguageContext);
    if (!context) {
        throw new Error('useLanguage must be used within LanguageProvider');
    }
    return context;
}
