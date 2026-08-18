import { useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, Loader2, Pencil, Play, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import VoicePickerButton from "@/components/voices/VoicePickerButton";
import { cn } from "@/components/ui/utils";
import type { SubtitleOverlayStyle } from "@/components/subtitleOverlay";
import { SOURCE_LANGUAGES, TARGET_LANGUAGES, languageLabel } from "@/pages/Automation/automation";
import type { WorkspaceContext } from "./types";
import { CheckRow, LabeledSelect } from "./ui";
import ConfirmDialog from "./ConfirmDialog";
import type { StageKey } from "@/pages/Automation/automation";
import {
  CUSTOM_TOOLS,
  stageMeta,
  stagesForTools,
  type ActiveCustomTool,
  type CustomToolConfig,
  type CustomToolId,
  toolDef,
} from "./customTools";

/**
 * CUSTOM TOOL WORKSPACE — the right-hand tool panel of the Custom page.
 *
 * Flow: pick a tool → configure it in the panel → Apply → the tool becomes an
 * ACTIVE TOOL card → Run starts a REAL pipeline job (the same engine as
 * Automation). Every control here is wired to real data (providers, voices,
 * video metadata); nothing is mocked.
 */
export default function CustomToolPanel({
  ctx,
  tools,
  onApply,
  onRemove,
  onReset,
}: {
  ctx: WorkspaceContext;
  tools: ActiveCustomTool[];
  onApply: (tool: ActiveCustomTool) => void;
  onRemove: (id: CustomToolId) => void;
  onReset: () => void;
}) {
  const [configuring, setConfiguring] = useState<CustomToolId | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const { phase } = ctx;
  const running = phase === "running";

  // Which tool is being configured right now (null → the card grid).
  const current = configuring ? toolDef(configuring) : null;
  // The dependency-ordered stage chain Run will execute (system-decided).
  const pipeline = useMemo(() => stagesForTools(tools), [tools]);

  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-l border-border bg-panel">
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Tools
          </p>
          {tools.length > 0 && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setConfirmReset(true)}
              title="Remove all tools"
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>

        {configuring && current ? (
          <ConfigPanel
            ctx={ctx}
            toolId={configuring}
            initial={tools.find((t) => t.id === configuring)?.config ?? {}}
            onCancel={() => setConfiguring(null)}
            onApply={(config) => {
              onApply({ id: configuring, config });
              setConfiguring(null);
            }}
          />
        ) : (
          <>
            <div className="mt-2 grid grid-cols-2 gap-1.5">
              {CUSTOM_TOOLS.map((tool) => {
                const active = tools.some((t) => t.id === tool.id);
                const Icon = tool.icon;
                return (
                  <button
                    key={tool.id}
                    type="button"
                    data-role={`custom-tool-${tool.id}`}
                    disabled={running}
                    onClick={() => setConfiguring(tool.id)}
                    className={cn(
                      "flex items-center gap-2 rounded-md border px-2 py-2 text-left transition-colors",
                      active
                        ? "border-gold/50 bg-gold-soft/30"
                        : "border-border bg-card hover:bg-accent/40",
                      running && "opacity-60",
                    )}
                  >
                    <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">{tool.name}</span>
                    {active && (
                      <Check className="size-3 shrink-0 text-emerald-400" aria-hidden="true" />
                    )}
                  </button>
                );
              })}
            </div>

            {tools.length > 0 && (
              <div className="mt-3 space-y-1">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Active
                </p>
                {tools.map((tool) => (
                  <ActiveToolRow
                    key={tool.id}
                    tool={tool}
                    onEdit={() => setConfiguring(tool.id)}
                    onRemove={() => onRemove(tool.id)}
                  />
                ))}
              </div>
            )}

            {pipeline.length > 0 && (
              <div className="mt-3">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Pipeline
                </p>
                <PipelinePreview ctx={ctx} stages={pipeline} tools={tools} />
                {tools.length > 1 && (
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    Stages are merged &amp; deduplicated — one run, dependency order.
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>

      <div className="shrink-0 space-y-1.5 border-t border-border bg-panel p-3">
        <Button
          type="button"
          data-role="custom-run"
          disabled={running || ctx.busy || tools.length === 0}
          onClick={() => ctx.actions.automate()}
          className="w-full bg-gold text-gold-foreground hover:bg-gold/90 disabled:opacity-60"
        >
          {running ? (
            <>
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              {Math.round(ctx.overallProgress * 100)}%
            </>
          ) : (
            <>
              <Play className="size-4" aria-hidden="true" /> Run
            </>
          )}
        </Button>
        {(phase === "running" || phase === "failed" || phase === "cancelled") && (
          <div className="flex gap-1.5">
            {phase === "running" && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="flex-1"
                onClick={() => ctx.actions.cancel()}
              >
                <X className="size-3.5" aria-hidden="true" /> Cancel
              </Button>
            )}
            {phase === "failed" && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="flex-1"
                onClick={() => ctx.actions.retry()}
              >
                Retry
              </Button>
            )}
            {(phase === "failed" || phase === "cancelled") && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="flex-1"
                onClick={() => ctx.actions.reprocess()}
              >
                Restart
              </Button>
            )}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmReset}
        title="Reset all tools?"
        message="Remove every configured tool from this workspace. The video and project stay untouched."
        confirmLabel="Reset tools"
        onCancel={() => setConfirmReset(false)}
        onConfirm={() => {
          onReset();
          setConfirmReset(false);
        }}
      />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// PIPELINE PREVIEW — the stage chain Run will execute, in dependency order.
// The currently-running stage glows; succeeded stages show a check.
// ---------------------------------------------------------------------------

function PipelinePreview({
  ctx,
  stages,
  tools,
}: {
  ctx: WorkspaceContext;
  stages: StageKey[];
  tools: ActiveCustomTool[];
}) {
  return (
    <div data-role="pipeline-preview" className="mt-1.5 flex flex-wrap items-center gap-y-1.5">
      {stages.map((key, index) => {
        const { label, icon: Icon } = stageMeta(key);
        // The first applied tool that owns this stage contributes it — when
        // two tools share a stage, the chip appears once (dedupe) and the tag
        // shows the tool that supplies its config.
        const owner = tools.find((t) => toolDef(t.id).stages.includes(key));
        const stage = ctx.stages.find((s) => s.key === key);
        const running = stage?.status === "running";
        const done = stage?.status === "succeeded";
        return (
          <div key={key} className="flex items-center gap-1">
            {index > 0 && (
              <ChevronRight
                className="size-3 shrink-0 text-muted-foreground/50"
                aria-hidden="true"
              />
            )}
            <div className="flex flex-col items-center gap-0.5">
              <span
                data-role={`pipeline-stage-${key}`}
                className={cn(
                  "flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium",
                  running
                    ? "border-gold/60 bg-gold/15 text-gold"
                    : done
                      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                      : "border-border bg-card text-muted-foreground",
                )}
              >
                <Icon className="size-3" aria-hidden="true" />
                {label}
                {done && <Check className="size-3 shrink-0 text-emerald-400" aria-hidden="true" />}
              </span>
              {owner && (
                <span
                  data-role={`pipeline-stage-${key}-owner`}
                  className="max-w-[90px] truncate text-[9px] text-muted-foreground/70"
                  title={owner.id}
                >
                  {toolDef(owner.id).name}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
// ---------------------------------------------------------------------------
// ACTIVE TOOL ROW — the small applied-tool card ([Edit] [Remove])
// ---------------------------------------------------------------------------

function ActiveToolRow({
  tool,
  onEdit,
  onRemove,
}: {
  tool: ActiveCustomTool;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const def = toolDef(tool.id);
  const summary = toolSummary(tool);
  return (
    <div
      data-role={`active-tool-${tool.id}`}
      className="flex items-center gap-1.5 rounded border border-border px-2 py-1.5"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-xs font-medium">{def.name}</p>
        {summary && <p className="truncate text-[10px] text-muted-foreground">{summary}</p>}
      </div>
      <button
        type="button"
        onClick={onEdit}
        title="Edit"
        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <Pencil className="size-3.5" aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={onRemove}
        title="Remove"
        className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-red-400"
      >
        <X className="size-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}

function toolSummary(tool: ActiveCustomTool): string {
  const c = tool.config;
  switch (tool.id) {
    case "audio-separate":
      return c.audioMode === "normalize"
        ? "Normalize"
        : c.audioMode === "denoise"
          ? "Denoise"
          : "Vocal removal";
    case "subtitle-generate":
      return c.sourceLanguage ? languageLabel(c.sourceLanguage) : "Auto";
    case "dub":
      return languageLabel(c.dubTargetLanguage ?? "vi");
    case "translate-video":
      return `${languageLabel(c.translateSourceLanguage ?? "")} → ${languageLabel(c.translateTargetLanguage ?? "vi")}`;
    case "burn-subtitles":
      return c.overlay ? `${c.overlay.font} ${c.overlay.fontSizePlayRes}px` : "Default";
    case "logo-remove":
      return c.logo ? `${c.logo.width}×${c.logo.height}` : "";
  }
}

function ConfigPanel({
  ctx,
  toolId,
  initial,
  onCancel,
  onApply,
}: {
  ctx: WorkspaceContext;
  toolId: CustomToolId;
  initial: CustomToolConfig;
  onCancel: () => void;
  onApply: (config: CustomToolConfig) => void;
}) {
  const def = toolDef(toolId);
  const ToolIcon = def.icon;
  const [draft, setDraft] = useState<CustomToolConfig>(initial);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // While the Xóa logo config is open, mirror the draft region into the
  // shared workspace state so the large-preview rectangle stays in sync with
  // the number inputs (and dragging on the video updates them live).
  useEffect(() => {
    if (toolId !== "logo-remove") return;
    ctx.logoRegion.setRegion(draft.logo ?? { x: 0, y: 0, width: 64, height: 64 });
    return () => ctx.logoRegion.setRegion(null);
  }, [toolId, draft.logo, ctx.logoRegion.setRegion]);

  // And the reverse: a drag on the large preview updates the shared region,
  // which must flow back into this panel's draft so the number inputs and the
  // Apply payload follow the rectangle. Equality-guarded to stop the two-way
  // sync from ping-ponging (drag → draft → region → …).
  useEffect(() => {
    if (toolId !== "logo-remove") return;
    const r = ctx.logoRegion.region;
    if (!r) return;
    setDraft((d) => {
      const cur = d.logo;
      if (
        cur &&
        cur.x === r.x &&
        cur.y === r.y &&
        cur.width === r.width &&
        cur.height === r.height
      ) {
        return d;
      }
      return { ...d, logo: { ...r } };
    });
  }, [toolId, ctx.logoRegion.region]);

  return (
    <div data-role={`config-${toolId}`} className="mt-2 space-y-3">
      <div className="flex items-center gap-2">
        <ToolIcon className="size-4 shrink-0" aria-hidden="true" />
        <p className="text-sm font-semibold">{def.name}</p>
        <button
          type="button"
          onClick={onCancel}
          className="ml-auto rounded p-1 text-muted-foreground hover:bg-accent"
          aria-label="Close"
        >
          <X className="size-4" aria-hidden="true" />
        </button>
      </div>

      {toolId === "audio-separate" && <AudioSeparatePanel draft={draft} setDraft={setDraft} />}
      {toolId === "subtitle-generate" && (
        <SubtitleGeneratePanel
          draft={draft}
          setDraft={setDraft}
          showAdvanced={showAdvanced}
          ctx={ctx}
        />
      )}
      {toolId === "dub" && (
        <DubPanel ctx={ctx} draft={draft} setDraft={setDraft} showAdvanced={showAdvanced} />
      )}
      {toolId === "translate-video" && (
        <TranslateVideoPanel
          ctx={ctx}
          draft={draft}
          setDraft={setDraft}
          showAdvanced={showAdvanced}
        />
      )}
      {toolId === "burn-subtitles" && (
        <BurnSubtitlesPanel
          ctx={ctx}
          draft={draft}
          setDraft={setDraft}
          showAdvanced={showAdvanced}
        />
      )}
      {toolId === "logo-remove" && <LogoRemovePanel ctx={ctx} draft={draft} setDraft={setDraft} />}

      {(toolId === "subtitle-generate" ||
        toolId === "dub" ||
        toolId === "translate-video" ||
        toolId === "burn-subtitles") && (
        <button
          type="button"
          className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          onClick={() => setShowAdvanced((v) => !v)}
        >
          {showAdvanced ? "Hide advanced" : "Advanced"}
        </button>
      )}

      <div className="flex justify-end gap-2 border-t border-border pt-2.5">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" size="sm" data-role="apply-tool" onClick={() => onApply(draft)}>
          Apply
        </Button>
      </div>
    </div>
  );
}

type DraftProps = {
  ctx: WorkspaceContext;
  draft: CustomToolConfig;
  setDraft: (updater: (prev: CustomToolConfig) => CustomToolConfig) => void;
  showAdvanced?: boolean;
};

function AudioSeparatePanel({
  draft,
  setDraft,
}: {
  draft: CustomToolConfig;
  setDraft: DraftProps["setDraft"];
}) {
  return (
    <LabeledSelect
      label="Mode"
      value={draft.audioMode ?? "vocal_removal"}
      onChange={(v) => setDraft((d) => ({ ...d, audioMode: v as CustomToolConfig["audioMode"] }))}
      options={[
        { value: "vocal_removal", label: "Vocal removal" },
        { value: "normalize", label: "Normalize" },
        { value: "denoise", label: "Denoise" },
      ]}
    />
  );
}

function SubtitleGeneratePanel({ ctx, draft, setDraft, showAdvanced }: DraftProps) {
  return (
    <>
      <LabeledSelect
        label="Source language"
        value={draft.sourceLanguage ?? ""}
        onChange={(v) => setDraft((d) => ({ ...d, sourceLanguage: v }))}
        options={SOURCE_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
      />
      {showAdvanced && <StyleControls ctx={ctx} draft={draft} setDraft={setDraft} />}
    </>
  );
}

function DubPanel({ ctx, draft, setDraft, showAdvanced }: DraftProps) {
  const { options } = ctx;
  return (
    <>
      <LabeledSelect
        label="Language"
        value={draft.dubTargetLanguage ?? "vi"}
        onChange={(v) => setDraft((d) => ({ ...d, dubTargetLanguage: v }))}
        options={TARGET_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
      />
      <VoicePickerButton
        label="Voice"
        value={draft.dubVoice ?? options.voice}
        onSelect={(voiceId, engine) =>
          setDraft((d) => ({ ...d, dubVoice: voiceId, dubEngine: engine }))
        }
      />
      <LabeledSelect
        label="Provider"
        value={draft.dubProvider ?? options.provider}
        onChange={(v) => setDraft((d) => ({ ...d, dubProvider: v }))}
        options={options.providerOptions.map((p) => ({ value: p.id, label: p.name }))}
      />
      {showAdvanced && (
        <>
          <LabeledSelect
            label="Source language"
            value={draft.dubSourceLanguage ?? ""}
            onChange={(v) => setDraft((d) => ({ ...d, dubSourceLanguage: v }))}
            options={SOURCE_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
          />
          <CheckRow
            label="Keep background music"
            checked={draft.keepBackgroundMusic ?? true}
            onChange={(v) => setDraft((d) => ({ ...d, keepBackgroundMusic: v }))}
          />
        </>
      )}
    </>
  );
}

function TranslateVideoPanel({ ctx, draft, setDraft, showAdvanced }: DraftProps) {
  const { options } = ctx;
  const providerId = draft.translateProvider ?? options.provider;
  const showDub = draft.generateDub ?? false;
  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        <LabeledSelect
          label="Source"
          value={draft.translateSourceLanguage ?? ""}
          onChange={(v) => setDraft((d) => ({ ...d, translateSourceLanguage: v }))}
          options={SOURCE_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
        />
        <LabeledSelect
          label="Target"
          value={draft.translateTargetLanguage ?? "vi"}
          onChange={(v) => setDraft((d) => ({ ...d, translateTargetLanguage: v }))}
          options={TARGET_LANGUAGES.map((l) => ({ value: l.code, label: l.label }))}
        />
      </div>
      <LabeledSelect
        label="Provider"
        value={providerId}
        onChange={(v) => setDraft((d) => ({ ...d, translateProvider: v }))}
        options={options.providerOptions.map((p) => ({ value: p.id, label: p.name }))}
      />
      {showAdvanced && (
        <div className="space-y-1.5">
          <CheckRow
            label="Generate subtitles"
            checked={draft.generateSubtitles ?? true}
            onChange={(v) => setDraft((d) => ({ ...d, generateSubtitles: v }))}
          />
          <CheckRow
            label="Burn subtitles"
            checked={draft.burnSubtitles ?? true}
            onChange={(v) => setDraft((d) => ({ ...d, burnSubtitles: v }))}
          />
          <CheckRow
            label="Generate dubbed audio"
            checked={showDub}
            onChange={(v) => setDraft((d) => ({ ...d, generateDub: v }))}
          />
          {showDub && (
            <VoicePickerButton
              label="Voice"
              value={draft.dubVoice ?? options.voice}
              onSelect={(voiceId, engine) =>
                setDraft((d) => ({ ...d, dubVoice: voiceId, dubEngine: engine }))
              }
            />
          )}
        </div>
      )}
    </>
  );
}

function BurnSubtitlesPanel({ ctx, draft, setDraft, showAdvanced }: DraftProps) {
  const hasCues = ctx.cues.cues.length > 0;
  return (
    <>
      {!hasCues && (
        <p className="text-[11px] text-amber-300">
          No subtitles yet — run “Tạo phụ đề” or “Dịch video” first.
        </p>
      )}
      <p className="text-[11px] text-muted-foreground">
        Drag the caption in the preview to move it.
      </p>
      {showAdvanced ? (
        <StyleControls ctx={ctx} draft={draft} setDraft={setDraft} />
      ) : (
        <LabeledSelect
          label="Position"
          value={(draft.overlay ?? ctx.overlay).position}
          onChange={(v) =>
            setDraft((d) => ({
              ...d,
              overlay: {
                ...(d.overlay ?? ctx.overlay),
                position: v as SubtitleOverlayStyle["position"],
              },
            }))
          }
          options={[
            { value: "bottom_center", label: "Bottom" },
            { value: "top_center", label: "Top" },
            { value: "custom", label: "Custom (drag)" },
          ]}
        />
      )}
    </>
  );
}

function StyleControls({ ctx, draft, setDraft }: DraftProps) {
  const style = draft.overlay ?? ctx.overlay;
  const applyStyle = (patch: Partial<SubtitleOverlayStyle>) =>
    setDraft((d) => ({ ...d, overlay: { ...style, ...patch } }));
  return (
    <div className="space-y-2">
      <LabeledSelect
        label="Font"
        value={style.font}
        onChange={(v) => applyStyle({ font: v })}
        options={[
          { value: "Arial", label: "Arial" },
          { value: "Segoe UI", label: "Segoe UI" },
          { value: "Roboto", label: "Roboto" },
          { value: "Georgia", label: "Georgia" },
        ]}
      />
      <div className="grid grid-cols-2 items-end gap-2">
        <label className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wide text-muted-foreground">
            Size
          </span>
          <input
            type="number"
            min={24}
            max={96}
            value={style.fontSizePlayRes}
            onChange={(e) =>
              applyStyle({ fontSizePlayRes: Math.max(16, Number(e.target.value) || 44) })
            }
            className="h-7 w-full rounded border border-input bg-background px-2 text-xs"
          />
        </label>
        <LabeledSelect
          label="Position"
          value={style.position}
          onChange={(v) => applyStyle({ position: v as SubtitleOverlayStyle["position"] })}
          options={[
            { value: "bottom_center", label: "Bottom" },
            { value: "top_center", label: "Top" },
            { value: "custom", label: "Custom (drag)" },
          ]}
        />
      </div>
      <CheckRow
        label="Background box"
        checked={style.bgBox}
        onChange={(v) => applyStyle({ bgBox: v })}
      />
    </div>
  );
}

function LogoRemovePanel({ ctx, draft, setDraft }: DraftProps) {
  const { meta, videoUrl } = ctx;
  const region = draft.logo ?? { x: 0, y: 0, width: 64, height: 64 };
  const setRegion = (patch: Partial<typeof region>) =>
    setDraft((d) => ({ ...d, logo: { ...region, ...patch } }));
  const hasVideo = Boolean(videoUrl && meta && meta.width > 0 && meta.height > 0);
  return (
    <>
      <div className="grid grid-cols-4 gap-1">
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
              data-role={`logo-${k}`}
              value={region[k]}
              onChange={(e) => setRegion({ [k]: Math.max(0, Number(e.target.value) || 0) })}
              className="h-6 w-full rounded border border-input bg-background px-1 text-[10px] tabular-nums"
            />
          </label>
        ))}
      </div>
      {hasVideo ? (
        <p className="rounded border border-border bg-card px-2 py-1.5 text-[11px] text-muted-foreground">
          Drag the <span className="font-medium text-amber-300">amber rectangle</span> on the video
          preview to the left to set the logo area — or type the coordinates above.
        </p>
      ) : (
        <p className="text-[11px] text-muted-foreground">Import a video to pick the region.</p>
      )}
    </>
  );
}
