import { useEffect, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  AudioLines,
  Captions,
  Clapperboard,
  FolderDown,
  BookMarked,
  Scissors,
  Stamp,
  Languages,
  Mic,
  FileAudio,
  Type,
  Zap,
  type LucideIcon,
} from "lucide-react";

import type { Project } from "@/api/project";
import { cn } from "@/components/ui/utils";
import DictionaryPage from "@/pages/Dictionary";
import SubtitleEditorView from "@/pages/Project/SubtitleEditorView";
import PreviewView from "@/pages/Project/PreviewView";
import ExportView from "@/pages/Project/ExportView";
import type { NavKey } from "@/components/layout/Sidebar";

export type ToolId =
  | "subtitles"
  | "dictionary"
  | "export"
  | "preview"
  | "watermark"
  | "voice"
  | "audio"
  | "video"
  | "translate";

/** A Tools request from another page (e.g. Automation → "Edit subtitles"). */
export type ToolRequest = { tool: ToolId; projectId?: string };

type ToolDef = {
  id: ToolId;
  name: string;
  description: string;
  icon: LucideIcon;
  /** Navigates to another page instead of an inline view. */
  goto?: NavKey;
};

type ToolCategory = { title: string; icon: LucideIcon; tools: ToolDef[] };

/** Real tools (backend-backed). Unavailable ones live in the planned section. */
const CATEGORIES: ToolCategory[] = [
  {
    title: "Video",
    icon: Clapperboard,
    tools: [
      {
        id: "export",
        name: "Export",
        description: "Copy the rendered video (QC-checked) and subtitle files to a folder.",
        icon: FolderDown,
      },
      {
        id: "preview",
        name: "Preview & Overlay",
        description: "Stream a project video with the caption overlay before rendering.",
        icon: Clapperboard,
      },
      {
        id: "watermark",
        name: "Watermark",
        description: "Add a logo or text watermark while rendering (Automation → Branding).",
        icon: Stamp,
        goto: "automation",
      },
    ],
  },
  {
    title: "Audio",
    icon: AudioLines,
    tools: [
      {
        id: "audio",
        name: "Audio Extractor",
        description: "Extract the audio track as part of the automation pipeline.",
        icon: FileAudio,
        goto: "automation",
      },
    ],
  },
  {
    title: "Subtitle",
    icon: Captions,
    tools: [
      {
        id: "subtitles",
        name: "Subtitle Editor",
        description: "Generate, review and edit translated subtitles cue by cue.",
        icon: Captions,
      },
      {
        id: "video",
        name: "Subtitle Generator",
        description: "Generate subtitles from a transcript — runs in Automation.",
        icon: Type,
        goto: "automation",
      },
    ],
  },
  {
    title: "AI",
    icon: Zap,
    tools: [
      {
        id: "voice",
        name: "Speech-to-Text",
        description: "Transcribe speech locally with faster-whisper — runs in Automation.",
        icon: Mic,
        goto: "automation",
      },
      {
        id: "dictionary",
        name: "Dictionary & Glossary",
        description: "Manage glossary terms and character notes that shape translation.",
        icon: BookMarked,
      },
      {
        id: "translate",
        name: "Translation",
        description: "Translate a transcript with the selected provider — runs in Automation.",
        icon: Languages,
        goto: "automation",
      },
    ],
  },
];

/** Not-yet-implemented tools — listed honestly, never as working buttons. */
const PLANNED = [
  "Video Cutter",
  "Video Converter",
  "Logo Remover",
  "Audio Separator",
  "Audio Mixer",
  "Voice Generator",
  "Voice Dubbing",
] as const;

const TOOL_VIEWS: Partial<Record<ToolId, (projectId?: string) => ReactNode>> = {
  subtitles: (projectId) => <SubtitleEditorView projectId={projectId} />,
  dictionary: () => <DictionaryPage />,
  export: () => <ExportView />,
  preview: (projectId) => <PreviewView projectId={projectId} />,
};

interface ToolsPageProps {
  request: ToolRequest | null;
  onConsumeRequest: () => void;
  project: Project | null;
  onNavigate: (key: NavKey) => void;
}

export default function ToolsPage({
  request,
  onConsumeRequest,
  project,
  onNavigate,
}: ToolsPageProps) {
  const [activeTool, setActiveTool] = useState<ToolId | null>(null);

  // Honor external tool requests (e.g. Automation → "Edit subtitles").
  useEffect(() => {
    if (request) {
      setActiveTool(request.tool);
      onConsumeRequest();
    }
  }, [request, onConsumeRequest]);

  const activeDef = CATEGORIES.flatMap((c) => c.tools).find((t) => t.id === activeTool);

  if (activeTool && activeDef && !activeDef.goto && TOOL_VIEWS[activeTool]) {
    const projectId = request?.projectId ?? project?.id;
    return (
      <section aria-labelledby="tools-heading" className="mx-auto max-w-5xl space-y-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setActiveTool(null)}
            className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3.5" aria-hidden="true" /> Tools
          </button>
          <div>
            <h1 id="tools-heading" className="text-xl font-semibold tracking-tight">
              {activeDef.name}
            </h1>
            <p className="text-sm text-muted-foreground">{activeDef.description}</p>
          </div>
        </div>
        {TOOL_VIEWS[activeTool]?.(projectId)}
      </section>
    );
  }

  return (
    <section aria-labelledby="tools-heading" className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 id="tools-heading" className="text-2xl font-semibold tracking-tight">
          Tools
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Single-purpose utilities — run a task without the full automation pipeline. Only tools
          backed by the current backend are enabled.
        </p>
      </div>

      {CATEGORIES.map((category) => {
        const CategoryIcon = category.icon;
        return (
          <section key={category.title} aria-labelledby={`tools-${category.title}`}>
            <h2
              id={`tools-${category.title}`}
              className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            >
              <CategoryIcon className="size-3.5" aria-hidden="true" />
              {category.title}
            </h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {category.tools.map((tool) => {
                const Icon = tool.icon;
                return (
                  <button
                    key={`${category.title}-${tool.id}`}
                    type="button"
                    data-role={`tool-${tool.id}`}
                    onClick={() => {
                      if (tool.goto) onNavigate(tool.goto);
                      else setActiveTool(tool.id);
                    }}
                    className={cn(
                      "flex flex-col items-start gap-2 rounded-lg border border-border bg-card p-4 text-left transition-colors hover:border-primary/50 hover:bg-accent/40",
                    )}
                  >
                    <span className="flex items-center gap-2">
                      <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                      <span className="text-sm font-semibold">{tool.name}</span>
                    </span>
                    <span className="text-xs text-muted-foreground">{tool.description}</span>
                    {tool.goto && (
                      <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-sky-400/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-sky-400">
                        <Zap className="size-3" aria-hidden="true" /> Runs in Automation
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}

      {/* Honest planned list — not rendered as working buttons. */}
      <section
        aria-labelledby="tools-planned"
        className="rounded-lg border border-dashed border-border bg-muted/20 p-4"
      >
        <h2
          id="tools-planned"
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          Planned — not in this build
        </h2>
        <div className="mt-2 flex flex-wrap gap-2">
          {PLANNED.map((name) => (
            <span
              key={name}
              className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground"
            >
              <Scissors className="size-3" aria-hidden="true" />
              {name}
              <span className="text-[9px] uppercase tracking-wide text-muted-foreground/70">
                later
              </span>
            </span>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Dubbing, audio separation, OCR/logo removal and video editing arrive with their respective
          backend stages.
        </p>
      </section>
    </section>
  );
}
