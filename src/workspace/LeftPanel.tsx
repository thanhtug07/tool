import { useEffect, useRef, useState } from "react";
import {
  FileVideo,
  FolderOpen,
  Loader2,
  MoreHorizontal,
  Play,
  RotateCcw,
  Settings2,
  Upload,
  X,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import VoicePickerButton from "@/components/voices/VoicePickerButton";
import { cn } from "@/components/ui/utils";
import { SOURCE_LANGUAGES, TARGET_LANGUAGES } from "@/pages/Automation/automation";
import { fileBaseName } from "@/lib/format";
import type { WorkspaceContext } from "./types";
import { CheckRow, LabeledSelect } from "./ui";

interface LeftPanelProps {
  ctx: WorkspaceContext;
  onPickVideo: () => void;
  /** Show the rendered output in the Result preview tab and play it. */
  onPreviewResult: () => void;
}

/**
 * AUTOMATION control bar — Language / Provider / Voice + AUTOMATE.
 * Advanced options live behind More Options (progressive disclosure).
 */
export default function LeftPanel({ ctx, onPickVideo, onPreviewResult }: LeftPanelProps) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!moreOpen) return;
    function onDoc(e: MouseEvent) {
      if (moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setMoreOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [moreOpen]);

  const { project, phase, options } = ctx;
  const running = phase === "running";
  const outputReady = Boolean(ctx.resultUrl && ctx.artifacts);

  return (
    <div data-role="automation-bar" className="shrink-0 border-t border-border bg-panel">
      {/* Compact video / open row */}
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-1.5 text-xs text-muted-foreground">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={ctx.busy}
          onClick={onPickVideo}
          data-role="open-video"
        >
          {project ? (
            <>
              <FileVideo className="size-3.5" aria-hidden="true" /> Replace
            </>
          ) : (
            <>
              <Upload className="size-3.5" aria-hidden="true" /> Open Video
            </>
          )}
        </Button>
        {outputReady && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={onPreviewResult}
            data-role="automation-output-video"
            title={ctx.artifacts?.renderedVideo}
            className="max-w-[220px] gap-1.5 text-gold"
          >
            <Play className="size-3.5 shrink-0 fill-current" aria-hidden="true" />
            <span className="truncate">{fileBaseName(ctx.artifacts?.renderedVideo ?? "")}</span>
          </Button>
        )}
        {project && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={!outputReady}
            onClick={() => ctx.actions.openOutputFolder()}
            data-role="automation-output"
            title={outputReady ? ctx.artifacts?.renderedVideo : "Output is not ready yet"}
          >
            <FolderOpen className="size-3.5" aria-hidden="true" /> Output
          </Button>
        )}
        {project ? (
          <span className="min-w-0 truncate" title={project.source_video_path}>
            {fileBaseName(project.source_video_path)}
          </span>
        ) : (
          <span>Choose a video to start</span>
        )}
      </div>

      {/* Primary controls */}
      <div className="flex flex-wrap items-end gap-2 px-3 py-2">
        <div className="min-w-[110px] flex-1">
          <LabeledSelect
            label="Language"
            value={options.targetLanguage}
            onChange={options.setTargetLanguage}
            options={TARGET_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
          />
        </div>
        <div className="min-w-[130px] flex-1">
          <LabeledSelect
            label="Provider"
            value={options.provider}
            onChange={options.setProvider}
            options={options.providerOptions.map((p) => ({ value: p.id, label: p.name }))}
          />
        </div>
        <div className="min-w-[140px] flex-1">
          <VoicePickerButton
            label="Voice"
            value={options.dubAudio ? options.voice : ""}
            allowNone
            disabled={options.voiceOptions.length === 0}
            onSelect={(voiceId, engine) => {
              if (!voiceId) {
                // "No dubbing": clear the voice so state stays consistent
                // (a voice without dubbing would resurrect on next seed).
                options.setVoice("");
                options.setDubAudio(false);
                return;
              }
              options.setVoice(voiceId);
              options.setTtsEngine(engine);
              options.setDubAudio(true);
            }}
          />
        </div>

        <div className="relative" ref={moreRef}>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-role="more-options"
            onClick={() => setMoreOpen((o) => !o)}
          >
            <Settings2 className="size-3.5" aria-hidden="true" /> More
          </Button>
          {moreOpen && <MoreOptionsPanel ctx={ctx} onClose={() => setMoreOpen(false)} />}
        </div>

        <Button
          type="button"
          data-role="automate-button"
          disabled={running || ctx.busy}
          onClick={() => ctx.actions.automate()}
          className={cn(
            "min-w-[140px] bg-gold text-gold-foreground hover:bg-gold/90 disabled:opacity-60",
          )}
        >
          {running ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              {Math.round(ctx.overallProgress * 100)}%
            </>
          ) : (
            <>
              <Zap className="size-4" aria-hidden="true" /> Automate
            </>
          )}
        </Button>
      </div>

      {(phase === "running" || phase === "failed" || phase === "cancelled") && (
        <div className="flex gap-1.5 px-3 pb-2">
          {phase === "running" && (
            <Button type="button" variant="outline" size="sm" onClick={() => ctx.actions.cancel()}>
              <X className="size-3.5" aria-hidden="true" /> Cancel
            </Button>
          )}
          {phase === "failed" && (
            <Button type="button" variant="outline" size="sm" onClick={() => ctx.actions.retry()}>
              <RotateCcw className="size-3.5" aria-hidden="true" /> Retry
            </Button>
          )}
          {(phase === "failed" || phase === "cancelled") && (
            <Button type="button" variant="ghost" size="sm" onClick={() => ctx.actions.reprocess()}>
              Restart
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function MoreOptionsPanel({ ctx, onClose }: { ctx: WorkspaceContext; onClose: () => void }) {
  const { options } = ctx;
  const cfg = options.logoRemoval;
  const [advanced, setAdvanced] = useState<"subtitle" | "voice" | "export" | null>(null);

  return (
    <div
      data-role="more-options-panel"
      className="absolute bottom-full right-0 z-40 mb-1 w-72 rounded-md border border-border bg-card p-3 shadow-lg"
    >
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold">Processing options</p>
        <button
          type="button"
          className="rounded p-0.5 text-muted-foreground hover:bg-accent"
          onClick={onClose}
          aria-label="Close"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      </div>

      <div className="space-y-1.5">
        <CheckRow
          label="Generate subtitles"
          checked={options.burnSubtitles}
          onChange={options.setBurnSubtitles}
        />
        <CheckRow label="Dub voice" checked={options.dubAudio} onChange={options.setDubAudio} />
        <CheckRow
          label="Remove logo"
          checked={cfg.enabled}
          onChange={(v) => options.setLogoRemoval({ ...cfg, enabled: v })}
        />
        <CheckRow
          label="Chunked processing (30s parallel)"
          checked={options.chunked}
          onChange={options.setChunked}
          hint="Split into 30s chunks, process in parallel, assemble + validate."
        />
      </div>

      {cfg.enabled && (
        <div className="mt-2 grid grid-cols-4 gap-1 rounded border border-border bg-background p-2">
          {(
            [
              { k: "x", label: "X" },
              { k: "y", label: "Y" },
              { k: "width", label: "W" },
              { k: "height", label: "H" },
            ] as const
          ).map(({ k, label }) => (
            <label key={k} className="flex items-center gap-1 text-[10px]">
              <span className="text-muted-foreground">{label}</span>
              <input
                type="number"
                min={0}
                value={cfg[k]}
                onChange={(e) =>
                  options.setLogoRemoval({ ...cfg, [k]: Math.max(0, Number(e.target.value) || 0) })
                }
                className="h-6 w-full rounded border border-input bg-background px-1 text-[10px] tabular-nums"
              />
            </label>
          ))}
        </div>
      )}

      <div className="mt-3 space-y-1 border-t border-border pt-2">
        <button
          type="button"
          className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          onClick={() => setAdvanced(advanced === "subtitle" ? null : "subtitle")}
        >
          <MoreHorizontal className="size-3.5" aria-hidden="true" /> Subtitle settings
        </button>
        {advanced === "subtitle" && (
          <div className="space-y-2 rounded border border-border bg-background/60 p-2">
            <LabeledSelect
              label="Source language"
              value={options.sourceLanguage}
              onChange={options.setSourceLanguage}
              options={SOURCE_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
            />
          </div>
        )}

        <button
          type="button"
          className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          onClick={() => setAdvanced(advanced === "voice" ? null : "voice")}
        >
          <MoreHorizontal className="size-3.5" aria-hidden="true" /> Voice settings
        </button>
        {advanced === "voice" && (
          <div className="space-y-2 rounded border border-border bg-background/60 p-2">
            <LabeledSelect
              label="TTS engine"
              value={options.ttsEngine}
              onChange={options.setTtsEngine}
              options={[
                { value: "edge", label: "edge-tts (online)" },
                { value: "piper", label: "piper (offline)" },
              ]}
            />
          </div>
        )}

        <button
          type="button"
          className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          onClick={() => setAdvanced(advanced === "export" ? null : "export")}
        >
          <MoreHorizontal className="size-3.5" aria-hidden="true" /> Export settings
        </button>
        {advanced === "export" && (
          <div className="space-y-2 rounded border border-border bg-background/60 p-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="w-full"
              disabled={ctx.phase !== "succeeded"}
              onClick={() => {
                ctx.actions.export();
                onClose();
              }}
            >
              Export video
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="w-full"
              onClick={() => {
                ctx.actions.copyPath();
                onClose();
              }}
            >
              Copy output path
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
