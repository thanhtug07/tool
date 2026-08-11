import { useState } from "react";

import JobFailBanner from "@/components/JobFailBanner";
import Sidebar, { type NavKey } from "@/components/layout/Sidebar";
import ProjectsPage from "@/pages/Projects";
import DictionaryPage from "@/pages/Dictionary";
import SubtitleEditorView from "@/pages/Project";
import PreviewView from "@/pages/Project/PreviewView";
import SettingsPage from "@/pages/Settings";
import AboutPage from "@/pages/About";
import type { Project } from "@/api/project";

export default function App() {
  const [active, setActive] = useState<NavKey>("projects");
  const [activeProject, setActiveProject] = useState<Project | null>(null);

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar active={active} onNavigate={setActive} />
      <main className="flex-1 overflow-y-auto p-6">
        {active === "projects" && <ProjectsPage onOpenProject={setActiveProject} />}
        {active === "dictionary" && <DictionaryPage />}
        {active === "subtitles" && <SubtitleEditorView projectId={activeProject?.id} />}
        {active === "preview" && (
          <PreviewView projectId={activeProject?.id} videoPath={activeProject?.source_video_path} />
        )}
        {active === "settings" && <SettingsPage />}
        {active === "about" && <AboutPage />}
      </main>
      <JobFailBanner />
    </div>
  );
}
