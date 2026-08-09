import { FolderKanban, Settings, Info } from "lucide-react";

import { cn } from "@/components/ui/utils";

export type NavKey = "projects" | "settings" | "about";

const NAV_ITEMS: { key: NavKey; label: string; icon: typeof FolderKanban }[] = [
  { key: "projects", label: "Projects", icon: FolderKanban },
  { key: "settings", label: "Settings", icon: Settings },
  { key: "about", label: "About", icon: Info },
];

interface SidebarProps {
  active: NavKey;
  onNavigate: (key: NavKey) => void;
}

export default function Sidebar({ active, onNavigate }: SidebarProps) {
  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="flex h-14 shrink-0 items-center gap-2 border-b border-sidebar-border px-4">
        <span className="size-6 rounded-md bg-primary" aria-hidden="true" />
        <span className="text-sm font-semibold tracking-tight">AI Video Localization</span>
      </div>
      <nav className="flex-1 space-y-1 p-2" aria-label="Main">
        {NAV_ITEMS.map(({ key, label, icon: Icon }) => {
          const selected = key === active;
          return (
            <button
              key={key}
              type="button"
              onClick={() => onNavigate(key)}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                selected
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
