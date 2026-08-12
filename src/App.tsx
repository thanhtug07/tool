import { useState } from "react";

import type { Project } from "@/api/project";
import JobFailBanner from "@/components/JobFailBanner";
import Sidebar, { type NavKey } from "@/components/layout/Sidebar";
import DashboardPage from "@/pages/Dashboard";
import AutomationPage from "@/pages/Automation";
import ToolsPage, { type ToolRequest } from "@/pages/Tools";
import SettingsPage from "@/pages/Settings";
import { JobsProvider } from "@/stores/jobs";

export default function App() {
  const [active, setActive] = useState<NavKey>("dashboard");
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [toolRequest, setToolRequest] = useState<ToolRequest | null>(null);

  function openTool(tool: ToolRequest["tool"], projectId?: string) {
    setToolRequest({ tool, projectId });
    setActive("tools");
  }

  function openProject(project: Project) {
    setActiveProject(project);
    setActive("automation");
  }

  return (
    <JobsProvider>
      <div className="flex h-screen overflow-hidden">
        <Sidebar active={active} onNavigate={setActive} />
        <main className="flex-1 overflow-y-auto p-6">
          {active === "dashboard" && (
            <DashboardPage onNavigate={setActive} onOpenProject={openProject} />
          )}
          {active === "automation" && (
            <AutomationPage
              project={activeProject}
              onProjectChange={setActiveProject}
              onNavigate={setActive}
              onOpenTool={openTool}
            />
          )}
          {active === "tools" && (
            <ToolsPage
              request={toolRequest}
              onConsumeRequest={() => setToolRequest(null)}
              project={activeProject}
              onNavigate={setActive}
            />
          )}
          {active === "settings" && <SettingsPage />}
        </main>
        <JobFailBanner />
      </div>
    </JobsProvider>
  );
}
