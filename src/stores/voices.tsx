import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { getTtsVoices, ttsPreview } from "@/api/voices";
import { toMediaUrl } from "@/api/media";
import { isTauri } from "@/lib/env";
import {
  EMPTY_VOICE_FILTERS,
  filterOptions,
  filterVoices,
  flattenVoices,
  pushRecent,
  type VoiceEntry,
  type VoiceFilters,
} from "@/lib/voiceLibrary";

const FAVORITES_KEY = "aivs.voice.favorites";
const RECENT_KEY = "aivs.voice.recent";

function readList(key: string): string[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeList(key: string, list: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify(list));
  } catch {
    // Storage can be unavailable (private mode) — best effort.
  }
}

export type PreviewState = {
  voiceId: string | null;
  status: "idle" | "loading" | "ready" | "error";
  /** Media URL once generated (cached across opens). */
  url: string | null;
  error: string | null;
};

export type VoicesContextValue = {
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  /** Every voice from every engine, flattened (real data). */
  voices: VoiceEntry[];
  filters: VoiceFilters;
  setFilters: (patch: Partial<VoiceFilters>) => void;
  /** Filter option values derived from the data. */
  options: ReturnType<typeof filterOptions>;
  filtered: VoiceEntry[];
  favorites: Set<string>;
  toggleFavorite: (voiceId: string) => void;
  recent: string[];
  markUsed: (voiceId: string) => void;
  /**
   * Resolve a playable URL for a voice preview — cached + single-flight so
   * rapid clicks never re-generate. Throws with the real error on failure.
   */
  previewUrl: (voice: VoiceEntry) => Promise<string>;
};

const VoicesContext = createContext<VoicesContextValue | null>(null);

export function VoicesProvider({ children }: { children: ReactNode }) {
  const [engines, setEngines] = useState<Awaited<ReturnType<typeof getTtsVoices>>["engines"]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFiltersState] = useState<VoiceFilters>(EMPTY_VOICE_FILTERS);
  const [favorites, setFavorites] = useState<Set<string>>(() => new Set(readList(FAVORITES_KEY)));
  const [recent, setRecent] = useState<string[]>(() => readList(RECENT_KEY));

  const reload = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const result = await getTtsVoices();
      setEngines(result.engines);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const voices = useMemo(() => flattenVoices(engines), [engines]);
  const options = useMemo(() => filterOptions(voices), [voices]);
  const filtered = useMemo(() => filterVoices(voices, filters), [voices, filters]);

  const setFilters = useCallback((patch: Partial<VoiceFilters>) => {
    setFiltersState((current) => ({ ...current, ...patch }));
  }, []);

  const toggleFavorite = useCallback((voiceId: string) => {
    setFavorites((current) => {
      const next = new Set(current);
      if (next.has(voiceId)) next.delete(voiceId);
      else next.add(voiceId);
      writeList(FAVORITES_KEY, [...next]);
      return next;
    });
  }, []);

  const markUsed = useCallback((voiceId: string) => {
    setRecent((current) => {
      const next = pushRecent(current, voiceId);
      writeList(RECENT_KEY, next);
      return next;
    });
  }, []);

  // Preview cache: key `engine:voice:text` → media URL; single-flight so
  // concurrent clicks on the same voice reuse the in-flight request.
  const cacheRef = useRef(new Map<string, string>());
  const inflightRef = useRef(new Map<string, Promise<string>>());

  const previewUrl = useCallback(async (voice: VoiceEntry): Promise<string> => {
    const text = voice.preview_text || "Hello, this is a voice preview for your video.";
    const key = `${voice.engine}:${voice.id}:${text}`;
    const cached = cacheRef.current.get(key);
    if (cached) return cached;
    const inflight = inflightRef.current.get(key);
    if (inflight) return inflight;
    const promise = (async () => {
      const result = await ttsPreview(voice.engine, voice.id, text);
      const url = toMediaUrl(result.path);
      cacheRef.current.set(key, url);
      return url;
    })();
    inflightRef.current.set(key, promise);
    try {
      return await promise;
    } finally {
      inflightRef.current.delete(key);
    }
  }, []);

  const value = useMemo<VoicesContextValue>(
    () => ({
      loading,
      error,
      reload,
      voices,
      filters,
      setFilters,
      options,
      filtered,
      favorites,
      toggleFavorite,
      recent,
      markUsed,
      previewUrl,
    }),
    [
      loading,
      error,
      reload,
      voices,
      filters,
      setFilters,
      options,
      filtered,
      favorites,
      toggleFavorite,
      recent,
      markUsed,
      previewUrl,
    ],
  );

  return <VoicesContext.Provider value={value}>{children}</VoicesContext.Provider>;
}

export function useVoices(): VoicesContextValue {
  const ctx = useContext(VoicesContext);
  if (!ctx) {
    throw new Error("useVoices must be used inside <VoicesProvider>");
  }
  return ctx;
}
