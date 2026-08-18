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
import type { NavKey } from "@/lib/nav";

export type ToolId =
  | "subtitles"
  | "dictionary"
  | "export"
  | "preview"
  | "watermark"
  | "voice"
  | "audio"
  | "audio-separator"
  | "logo-remover"
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
      {
        id: "logo-remover",
        name: "Logo Remover",
        description:
          "Remove a logo rectangle from the source with ffmpeg delogo — a real workflow step.",
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
      {
        id: "audio-separator",
        name: "Audio Separator",
        description: "Separate the vocal track (karaoke mix) with ffmpeg — a real workflow step.",
        icon: AudioLines,
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
const PLANNED = ["Video Cutter", "Video Converter", "Voice Generator", "Voice Dubbing"] as const;

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

  const projectId = request?.projectId ?? project?.id;
  const activeView = activeTool && activeDef && !activeDef.goto ? TOOL_VIEWS[activeTool] : null;

  return (
    <section aria-labelledby="tools-heading" className="flex h-full min-h-0 gap-4">
      {/* LEFT — compact tool panel (editor style, not giant cards) */}
      <nav aria-label="Tools" className="w-52 shrink-0 space-y-3 overflow-y-auto pr-1">
        <div>
          <h1 id="tools-heading" className="text-sm font-semibold tracking-tight">
            Tools
          </h1>
          <p className="mt-0.5 text-[11px] text-muted-foreground">Real backend tools only.</p>
        </div>
        {CATEGORIES.map((category) => {
          const CategoryIcon = category.icon;
          return (
            <div key={category.title}>
              <h2 className="mb-1 flex items-center gap-1.5 px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                <CategoryIcon className="size-3" aria-hidden="true" />
                {category.title}
              </h2>
              <ul className="space-y-0.5">
                {category.tools.map((tool) => {
                  const Icon = tool.icon;
                  const selected = activeTool === tool.id;
                  return (
                    <li key={`${category.title}-${tool.id}`}>
                      <button
                        type="button"
                        data-role={`tool-${tool.id}`}
                        onClick={() => {
                          if (tool.goto) onNavigate(tool.goto);
                          else setActiveTool(tool.id);
                        }}
                        title={tool.description}
                        className={cn(
                          "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs transition-colors",
                          selected
                            ? "bg-accent font-medium text-accent-foreground"
                            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                        )}
                      >
                        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                        <span className="truncate">{tool.name}</span>
                        {tool.goto && (
                          <Zap
                            className="ml-auto size-3 shrink-0 text-sky-400"
                            aria-hidden="true"
                          />
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
        <div className="rounded border border-dashed border-border bg-muted/20 p-2">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Planned — later
          </p>
          <ul className="mt-1 flex flex-wrap gap-1">
            {PLANNED.map((name) => (
              <li
                key={name}
                className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground/80"
              >
                <Scissors className="size-2.5" aria-hidden="true" />
                {name}
              </li>
            ))}
          </ul>
        </div>
      </nav>

      {/* RIGHT — tool workspace */}
      <div className="min-w-0 flex-1 overflow-y-auto rounded-lg border border-border bg-card p-4">
        {activeView ? (
          <>
            <div className="mb-4 flex items-center gap-3">
              <button
                type="button"
                onClick={() => setActiveTool(null)}
                className="flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="size-3.5" aria-hidden="true" /> Back
              </button>
              <div>
                <h2 className="text-sm font-semibold">{activeDef?.name}</h2>
                <p className="text-xs text-muted-foreground">{activeDef?.description}</p>
              </div>
            </div>
            {activeView(projectId)}
          </>
        ) : (
          <div className="grid h-full place-items-center p-6 text-center">
            <div className="space-y-1">
              <p className="text-sm font-medium">Select a tool</p>
              <p className="text-xs text-muted-foreground">
                Only tools backed by the current backend are listed. Tools that run inside the
                automation pipeline are marked with ⚡ and open the workspace.
              </p>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
