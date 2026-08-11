import { useState } from "react";

import Sidebar, { type NavKey } from "@/components/layout/Sidebar";
import ProjectsPage from "@/pages/Projects";
import DictionaryPage from "@/pages/Dictionary";
import SubtitleEditorView from "@/pages/Project";
import SettingsPage from "@/pages/Settings";
import AboutPage from "@/pages/About";

export default function App() {
  const [active, setActive] = useState<NavKey>("projects");

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar active={active} onNavigate={setActive} />
      <main className="flex-1 overflow-y-auto p-6">
        {active === "projects" && <ProjectsPage />}
        {active === "dictionary" && <DictionaryPage />}
        {active === "subtitles" && <SubtitleEditorView />}
        {active === "settings" && <SettingsPage />}
        {active === "about" && <AboutPage />}
      </main>
    </div>
  );
}
