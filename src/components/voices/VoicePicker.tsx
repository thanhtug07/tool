import { useEffect, useRef, useState } from "react";
import { Check, Loader2, Pause, Play, Search, Star, Volume2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/components/ui/utils";
import { languageLabel, voiceStatus, type VoiceEntry } from "@/lib/voiceLibrary";
import { useVoices } from "@/stores/voices";

/**
 * VOICE LIBRARY — one shared voice picker for the whole app (Automation bar,
 * Custom tools, Settings). Real voices from the worker registry; search,
 * language/gender/engine filters, favorites + recently used (persisted), and a
 * REAL preview (TTS synthesis through the worker, cached + single-flight).
 */
export default function VoicePicker({
  open,
  onClose,
  onSelect,
  selectedId,
  allowNone = false,
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (voice: VoiceEntry | null) => void;
  selectedId: string | null;
  allowNone?: boolean;
}) {
  const {
    loading,
    voices,
    filtered,
    options,
    filters,
    setFilters,
    favorites,
    toggleFavorite,
    recent,
    previewUrl,
  } = useVoices();
  const [showCount, setShowCount] = useState(24);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const tokenRef = useRef(0);
  const [preview, setPreview] = useState<{
    voiceId: string;
    status: "loading" | "ready" | "error";
    url: string | null;
    error: string | null;
  } | null>(null);

  // Reset preview + search when the modal opens.
  useEffect(() => {
    if (!open) return;
    setPreview(null);
    setShowCount(24);
    audioRef.current?.pause();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    return () => {
      tokenRef.current++;
      audioRef.current?.pause();
    };
  }, [open]);

  if (!open) return null;

  const play = async (voice: VoiceEntry) => {
    tokenRef.current++;
    const token = tokenRef.current;
    audioRef.current?.pause();
    setPreview({ voiceId: voice.id, status: "loading", url: null, error: null });
    try {
      const url = await previewUrl(voice);
      if (token !== tokenRef.current) return;
      setPreview({ voiceId: voice.id, status: "ready", url, error: null });
      const audio = audioRef.current;
      if (audio) {
        audio.src = url;
        await audio.play();
      }
    } catch (e) {
      if (token !== tokenRef.current) return;
      setPreview({ voiceId: voice.id, status: "error", url: null, error: String(e) });
    }
  };

  const stop = () => {
    tokenRef.current++;
    audioRef.current?.pause();
    setPreview(null);
  };

  const togglePlayPause = (voice: VoiceEntry) => {
    if (preview?.voiceId === voice.id) {
      if (preview.status === "ready") {
        const audio = audioRef.current;
        if (audio && !audio.paused) audio.pause();
        else void play(voice);
        return;
      }
      if (preview.status === "loading") {
        stop();
        return;
      }
    }
    void play(voice);
  };

  const activeVoice = preview?.voiceId
    ? (voices.find((v) => v.id === preview.voiceId) ?? null)
    : null;

  // Sections — favorites, then recently used, then everything (deduped).
  const favSection = filtered.filter((v) => favorites.has(v.id));
  const favIds = new Set(favSection.map((v) => v.id));
  const recentSection = filtered.filter((v) => recent.includes(v.id) && !favIds.has(v.id));
  const shownIds = new Set([...favIds, ...recentSection.map((v) => v.id)]);
  const allSection = filtered.filter((v) => !shownIds.has(v.id));

  const renderCard = (voice: VoiceEntry) => {
    const status = voiceStatus(voice);
    const available = status.status === "available";
    const isSelected = voice.id === selectedId;
    const isFav = favorites.has(voice.id);
    const isPreviewing = preview?.voiceId === voice.id;
    return (
      <div
        key={`${voice.engine}:${voice.id}`}
        data-role={`voice-card-${voice.id}`}
        className={cn(
          "flex items-center gap-2 rounded border border-border bg-card px-2.5 py-2",
          isSelected && "border-gold/60 bg-gold-soft/20",
          !available && "opacity-70",
        )}
      >
        <button
          type="button"
          data-role={`voice-fav-${voice.id}`}
          aria-label={isFav ? "Remove favorite" : "Add favorite"}
          onClick={() => toggleFavorite(voice.id)}
          className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-gold"
        >
          <Star className={cn("size-3.5", isFav && "fill-gold text-gold")} aria-hidden="true" />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <p className="truncate text-xs font-semibold">{voice.name}</p>
            {!available && (
              <span
                className="shrink-0 rounded-full bg-destructive/15 px-1.5 py-px text-[9px] font-medium text-red-300"
                title={status.reason}
              >
                Unavailable
              </span>
            )}
          </div>
          <p className="truncate text-[10px] text-muted-foreground">
            {languageLabel(voice.language)} · {voice.gender} · {voice.age}
            {voice.tags.length > 0 && (
              <span className="text-muted-foreground/70"> · {voice.tags.join(" / ")}</span>
            )}
          </p>
          <p className="truncate text-[10px] text-muted-foreground/70">
            {voice.providerLabel} · {voice.label}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            data-role={`voice-preview-${voice.id}`}
            disabled={!available}
            title={available ? undefined : status.reason}
            onClick={() => togglePlayPause(voice)}
            className="size-7 p-0"
          >
            {isPreviewing && preview.status === "loading" ? (
              <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            ) : isPreviewing && preview.status === "ready" && !audioRef.current?.paused ? (
              <Pause className="size-3.5" aria-hidden="true" />
            ) : (
              <Play className="size-3.5" aria-hidden="true" />
            )}
          </Button>
          <Button
            type="button"
            size="sm"
            data-role={`voice-select-${voice.id}`}
            disabled={!available}
            onClick={() => {
              onSelect(voice);
              onClose();
            }}
            className={cn(
              "h-7 px-2 text-[11px]",
              isSelected ? "bg-emerald-600/20 text-emerald-300 hover:bg-emerald-600/30" : "",
            )}
          >
            {isSelected ? (
              <>
                <Check className="size-3" aria-hidden="true" /> Selected
              </>
            ) : (
              "Select"
            )}
          </Button>
        </div>
      </div>
    );
  };

  const chip = (active: boolean, onClick: () => void, label: string) => (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors",
        active
          ? "border-gold/60 bg-gold/15 text-gold"
          : "border-border bg-card text-muted-foreground hover:bg-accent",
      )}
    >
      {label}
    </button>
  );

  return (
    <div
      data-role="voice-picker"
      role="dialog"
      aria-modal="true"
      aria-label="Voice Library"
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-border bg-panel shadow-2xl">
        {/* Header */}
        <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-3">
          <Volume2 className="size-4 text-gold" aria-hidden="true" />
          <p className="text-sm font-semibold">Voice Library</p>
          <span className="text-[10px] text-muted-foreground">{voices.length} real voices</span>
          <button
            type="button"
            data-role="voice-picker-close"
            onClick={onClose}
            aria-label="Close"
            className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>

        {/* Search + filters */}
        <div className="shrink-0 space-y-2 border-b border-border px-3 py-2">
          <div className="relative">
            <Search
              className="absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <input
              type="search"
              data-role="voice-search"
              value={filters.query}
              onChange={(e) => {
                setFilters({ query: e.target.value });
                setShowCount(24);
              }}
              placeholder="Search voice, language, style, provider…"
              className="h-8 w-full rounded border border-input bg-background pl-7 pr-2 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Language
            </span>
            {chip(filters.language === "all", () => setFilters({ language: "all" }), "All")}
            {options.languages.map((lang) =>
              chip(
                filters.language === lang,
                () => setFilters({ language: lang }),
                languageLabel(lang),
              ),
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Gender
            </span>
            {chip(filters.gender === "all", () => setFilters({ gender: "all" }), "All")}
            {options.genders.map((g) =>
              chip(
                filters.gender === g,
                () => setFilters({ gender: g }),
                g === "female" ? "Female" : "Male",
              ),
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Provider
            </span>
            {chip(filters.engine === "all", () => setFilters({ engine: "all" }), "All")}
            {options.engines.map((engine) =>
              chip(
                filters.engine === engine,
                () => setFilters({ engine }),
                engine === "edge" ? "Free (cloud)" : "Local",
              ),
            )}
          </div>
          {preview?.status === "error" && (
            <p className="text-[11px] text-red-300">Preview failed: {preview.error}</p>
          )}
        </div>

        {/* Voices */}
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
          {loading && (
            <p className="py-6 text-center text-xs text-muted-foreground">Loading voices…</p>
          )}
          {!loading && filtered.length === 0 && (
            <p className="py-6 text-center text-xs text-muted-foreground">
              No voices match — try clearing a filter.
            </p>
          )}

          {favSection.length > 0 && (
            <section data-role="voice-section-favorites">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Favorites
              </p>
              <div className="space-y-1">{favSection.slice(0, showCount).map(renderCard)}</div>
            </section>
          )}

          {recentSection.length > 0 && (
            <section data-role="voice-section-recent">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                Recently Used
              </p>
              <div className="space-y-1">{recentSection.slice(0, showCount).map(renderCard)}</div>
            </section>
          )}

          {allSection.length > 0 && (
            <section data-role="voice-section-all">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {favSection.length > 0 ? "All Voices" : "Voices"}
              </p>
              <div className="space-y-1">{allSection.slice(0, showCount).map(renderCard)}</div>
              {filtered.length > showCount && (
                <button
                  type="button"
                  data-role="voice-show-more"
                  onClick={() => setShowCount((c) => c + 40)}
                  className="mt-1 w-full rounded border border-border py-1 text-[11px] text-muted-foreground hover:bg-accent"
                >
                  Show {filtered.length - showCount} more
                </button>
              )}
            </section>
          )}
        </div>

        {/* Footer */}
        {allowNone && (
          <div className="shrink-0 border-t border-border p-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              data-role="voice-none"
              onClick={() => {
                onSelect(null);
                onClose();
              }}
            >
              No dubbing
            </Button>
          </div>
        )}
      </div>

      {/* Real preview playback (single element for the whole library). */}
      <audio
        ref={audioRef}
        data-role="voice-preview-audio"
        className="hidden"
        onEnded={() => setPreview((p) => (p ? { ...p, status: "ready" } : p))}
      />
      {activeVoice && preview?.status === "loading" && (
        <p className="absolute bottom-6 left-1/2 -translate-x-1/2 rounded-full bg-black/80 px-3 py-1 text-[11px] text-amber-200">
          Generating preview for {activeVoice.name}…
        </p>
      )}
    </div>
  );
}
