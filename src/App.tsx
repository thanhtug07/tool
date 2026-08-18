import { useCallback, useState } from "react";

import type { Project } from "@/api/project";
import JobFailBanner from "@/components/JobFailBanner";
import TopBar from "@/components/layout/TopBar";
import type { NavKey } from "@/lib/nav";
import HomePage from "@/pages/Home";
import ToolsPage, { type ToolRequest } from "@/pages/Tools";
import SettingsPage from "@/pages/Settings";
import { JobsProvider } from "@/stores/jobs";
import { ProvidersProvider } from "@/stores/providers";
import { VoicesProvider } from "@/stores/voices";
import StudioWorkspace from "@/workspace/StudioWorkspace";

export default function App() {
  const [active, setActive] = useState<NavKey>("home");
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [toolRequest, setToolRequest] = useState<ToolRequest | null>(null);

  const navigate = useCallback((key: NavKey) => {
    setActive(key);
  }, []);

  const openAutomation = useCallback((project: Project | null) => {
    setActiveProject(project);
    setActive("automation");
  }, []);

  /** Open a project's processing history from the project hub. */
  const openProcessing = useCallback((project: Project | null) => {
    setActiveProject(project);
    setActive("custom");
  }, []);

  function openTool(tool: ToolRequest["tool"], projectId?: string) {
    setToolRequest({ tool, projectId });
    setActive("tools");
  }

  return (
    <JobsProvider>
      <ProvidersProvider>
        <VoicesProvider>
          <div className="flex h-screen flex-col overflow-hidden">
            <TopBar
              active={active}
              onNavigate={navigate}
              project={activeProject}
              onOpenProject={openAutomation}
            />
            <div className="min-h-0 flex-1">
              {active === "home" && (
                <HomePage
                  project={activeProject}
                  onOpenAutomation={openAutomation}
                  onOpenProcessing={openProcessing}
                  onOpenTools={() => setActive("tools")}
                />
              )}
              {active === "automation" && (
                <StudioWorkspace
                  mode="automation"
                  project={activeProject}
                  onProjectChange={setActiveProject}
                  onNavigate={navigate}
                  onOpenTool={openTool}
                />
              )}
              {active === "custom" && (
                <StudioWorkspace
                  mode="custom"
                  project={activeProject}
                  onProjectChange={setActiveProject}
                  onNavigate={navigate}
                  onOpenTool={openTool}
                />
              )}
              {active === "tools" && (
                <main className="h-full overflow-y-auto p-6">
                  <ToolsPage
                    request={toolRequest}
                    onConsumeRequest={() => setToolRequest(null)}
                    project={activeProject}
                    onNavigate={navigate}
                  />
                </main>
              )}
              {active === "settings" && (
                <main className="h-full overflow-y-auto p-6">
                  <SettingsPage />
                </main>
              )}
            </div>
            <JobFailBanner />
          </div>
        </VoicesProvider>
      </ProvidersProvider>
    </JobsProvider>
  );
}
