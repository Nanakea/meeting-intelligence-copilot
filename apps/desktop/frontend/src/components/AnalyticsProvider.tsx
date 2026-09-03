'use client';

import React, { createContext, type ReactNode, useEffect } from 'react';
import { load } from '@tauri-apps/plugin-store';

import Analytics from '@/lib/analytics';

const LOCAL_ONLY_ANALYTICS_MIGRATION_KEY = 'analyticsLocalOnlyPilotV1';

interface AnalyticsProviderProps {
  children: ReactNode;
}

interface AnalyticsContextType {
  isAnalyticsOptedIn: boolean;
  setIsAnalyticsOptedIn: (optedIn: boolean) => void;
}

export const AnalyticsContext = createContext<AnalyticsContextType>({
  isAnalyticsOptedIn: false,
  setIsAnalyticsOptedIn: () => undefined,
});

export default function AnalyticsProvider({ children }: AnalyticsProviderProps) {
  useEffect(() => {
    void (async () => {
      try {
        const store = await load('analytics.json', {
          autoSave: false,
          defaults: { analyticsOptedIn: false },
        });
        await store.set('analyticsOptedIn', false);
        await store.set(LOCAL_ONLY_ANALYTICS_MIGRATION_KEY, true);
        await store.save();
        await Analytics.disable();
      } catch {
        console.error('Local-only analytics migration was unavailable');
      }
    })();
  }, []);

  return (
    <AnalyticsContext.Provider
      value={{ isAnalyticsOptedIn: false, setIsAnalyticsOptedIn: () => undefined }}
    >
      {children}
    </AnalyticsContext.Provider>
  );
}
