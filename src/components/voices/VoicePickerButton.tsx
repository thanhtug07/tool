import { useState } from "react";
import { ChevronDown, Volume2 } from "lucide-react";

import { cn } from "@/components/ui/utils";
import { useVoices } from "@/stores/voices";
import VoicePicker from "./VoicePicker";

/**
 * VOICE PICKER BUTTON — the single shared voice control. Renders the current
 * voice (or "No dubbing" when none is set) and opens the Voice Library picker
 * on click. Used by Automation, the Custom tools and Settings — one library
 * app-wide, never a duplicated selector.
 */
export default function VoicePickerButton({
  label = "Voice",
  value,
  onSelect,
  allowNone = false,
  disabled = false,
  compact = false,
  className,
}: {
  label?: string;
  /** Selected voice id (''/undefined = none). */
  value: string | null;
  /** Called with the chosen voice (id + engine) or null when "No dubbing". */
  onSelect: (voiceId: string, engine: string) => void;
  allowNone?: boolean;
  disabled?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const { voices, markUsed } = useVoices();
  const selected = voices.find((v) => v.id === value) ?? null;

  return (
    <div className={className}>
      <span className="mb-1 block text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <button
        type="button"
        data-role="voice-picker-button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        className={cn(
          "flex h-8 w-full items-center gap-1.5 rounded border border-input bg-background px-2 text-left text-xs transition-colors hover:bg-accent disabled:opacity-50",
          compact && "h-7",
        )}
        title="Change voice"
      >
        <Volume2 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">
          {selected
            ? `${selected.name} — ${selected.gender}`
            : allowNone
              ? "No dubbing"
              : "Select voice"}
        </span>
        <ChevronDown className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
      </button>
      <VoicePicker
        open={open}
        onClose={() => setOpen(false)}
        onSelect={(voice) => {
          if (voice) {
            markUsed(voice.id);
            onSelect(voice.id, voice.engine);
          } else {
            onSelect("", "edge");
          }
        }}
        selectedId={value}
        allowNone={allowNone}
      />
    </div>
  );
}
