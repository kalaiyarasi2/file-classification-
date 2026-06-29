import { create } from "zustand";
import { persist } from "zustand/middleware";

export type LogLevel = "INFO" | "WARN" | "ERROR" | "DEBUG";
export interface LogEntry {
  ts: string;
  level: LogLevel;
  source: string;
  message: string;
  meta?: any;
}

export interface ActivityEntry {
  id: string;
  ts: string;
  kind: "detection" | "extraction" | "classification" | "pipeline" | "drive" | "organise";
  title: string;
  detail: string;
  meta?: any;
}

export interface StatsState {
  totalFiles: number;
  processed: number;
  scanned: number;
  digital: number;
  ocrProcessed: number;
  classificationSuccess: number;
  failures: number;
  categoriesFound: Record<string, number>;
  totalProcessingMs: number;
  pipelineRuns: number;
  confidenceBuckets: number[]; // length 6: 0-2, 2-4, 4-6, 6-7, 7-8, 8-9, 9-10
  daily: { day: string; processed: number; failed: number }[];
}

interface AppState {
  logs: LogEntry[];
  activity: ActivityEntry[];
  stats: StatsState;
  addLog: (level: LogLevel, source: string, message: string, meta?: any) => void;
  addActivity: (e: Omit<ActivityEntry, "id" | "ts">) => void;
  clearLogs: () => void;
  recordDetection: (isDigital: boolean) => void;
  recordExtraction: (pdfType: string, ms: number) => void;
  recordClassification: (success: boolean, score: number, category: string, ms: number) => void;
  recordPipeline: (success: number, failed: number, categories: Record<string, number>) => void;
}

function todayKey(d = new Date()) {
  return d.toISOString().slice(0, 10);
}

function bumpDaily(daily: StatsState["daily"], success = 0, failed = 0) {
  const key = todayKey();
  const map = new Map(daily.map((d) => [d.day, { ...d }]));
  const cur = map.get(key) || { day: key, processed: 0, failed: 0 };
  cur.processed += success;
  cur.failed += failed;
  map.set(key, cur);
  // keep last 7 days
  const sorted = Array.from(map.values()).sort((a, b) => a.day.localeCompare(b.day));
  return sorted.slice(-7);
}

const initialStats: StatsState = {
  totalFiles: 0, processed: 0, scanned: 0, digital: 0, ocrProcessed: 0,
  classificationSuccess: 0, failures: 0, categoriesFound: {},
  totalProcessingMs: 0, pipelineRuns: 0,
  confidenceBuckets: [0, 0, 0, 0, 0, 0, 0],
  daily: [],
};

export const useApp = create<AppState>()(
  persist(
    (set, get) => ({
      logs: [],
      activity: [],
      stats: initialStats,
      addLog: (level, source, message, meta) =>
        set((s) => ({
          logs: [
            { ts: new Date().toISOString(), level, source, message, meta },
            ...s.logs,
          ].slice(0, 500),
        })),
      addActivity: (e) =>
        set((s) => ({
          activity: [
            { ...e, id: crypto.randomUUID(), ts: new Date().toISOString() },
            ...s.activity,
          ].slice(0, 100),
        })),
      clearLogs: () => set({ logs: [] }),
      recordDetection: (isDigital) =>
        set((s) => ({
          stats: {
            ...s.stats,
            totalFiles: s.stats.totalFiles + 1,
            processed: s.stats.processed + 1,
            digital: s.stats.digital + (isDigital ? 1 : 0),
            scanned: s.stats.scanned + (isDigital ? 0 : 1),
            daily: bumpDaily(s.stats.daily, 1, 0),
          },
        })),
      recordExtraction: (pdfType, ms) =>
        set((s) => ({
          stats: {
            ...s.stats,
            processed: s.stats.processed + 1,
            totalProcessingMs: s.stats.totalProcessingMs + ms,
            ocrProcessed: s.stats.ocrProcessed + (pdfType === "scanned" ? 1 : 0),
            digital: s.stats.digital + (pdfType === "digital" ? 1 : 0),
            scanned: s.stats.scanned + (pdfType === "scanned" ? 1 : 0),
            daily: bumpDaily(s.stats.daily, 1, 0),
          },
        })),
      recordClassification: (success, score, category, ms) =>
        set((s) => {
          const buckets = [...s.stats.confidenceBuckets];
          const idx = score < 2 ? 0 : score < 4 ? 1 : score < 6 ? 2 : score < 7 ? 3 : score < 8 ? 4 : score < 9 ? 5 : 6;
          buckets[idx] += 1;
          const cats = { ...s.stats.categoriesFound };
          if (success) cats[category] = (cats[category] || 0) + 1;
          return {
            stats: {
              ...s.stats,
              processed: s.stats.processed + 1,
              totalProcessingMs: s.stats.totalProcessingMs + ms,
              classificationSuccess: s.stats.classificationSuccess + (success ? 1 : 0),
              failures: s.stats.failures + (success ? 0 : 1),
              categoriesFound: cats,
              confidenceBuckets: buckets,
              daily: bumpDaily(s.stats.daily, success ? 1 : 0, success ? 0 : 1),
            },
          };
        }),
      recordPipeline: (success, failed, categories) =>
        set((s) => {
          const cats = { ...s.stats.categoriesFound };
          for (const [k, v] of Object.entries(categories)) cats[k] = (cats[k] || 0) + v;
          return {
            stats: {
              ...s.stats,
              pipelineRuns: s.stats.pipelineRuns + 1,
              processed: s.stats.processed + success + failed,
              classificationSuccess: s.stats.classificationSuccess + success,
              failures: s.stats.failures + failed,
              totalFiles: s.stats.totalFiles + success + failed,
              categoriesFound: cats,
              daily: bumpDaily(s.stats.daily, success, failed),
            },
          };
        }),
    }),
    {
      name: "fc_app_state",
      partialize: (s) => ({ logs: s.logs, activity: s.activity, stats: s.stats }),
    }
  )
);

// Theme + settings store
interface SettingsState {
  theme: "light" | "dark";
  animations: boolean;
  language: string;
  defaultInputFolder: string;
  defaultOutputFolder: string;
  setTheme: (t: "light" | "dark") => void;
  setAnimations: (b: boolean) => void;
  setLanguage: (l: string) => void;
  setDefaultInputFolder: (s: string) => void;
  setDefaultOutputFolder: (s: string) => void;
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: "light",
      animations: true,
      language: "English",
      defaultInputFolder: "/data/incoming",
      defaultOutputFolder: "/data/sorted",
      setTheme: (theme) => set({ theme }),
      setAnimations: (animations) => set({ animations }),
      setLanguage: (language) => set({ language }),
      setDefaultInputFolder: (s) => set({ defaultInputFolder: s }),
      setDefaultOutputFolder: (s) => set({ defaultOutputFolder: s }),
    }),
    { name: "fc_settings" }
  )
);
