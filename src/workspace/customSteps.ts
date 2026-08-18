import type { StageKey, StepConfig } from "@/pages/Automation/automation";

/**
 * Custom workflow step — kept as the pipeline engine's ordered step shape.
 * The Custom page is now a Tool Workspace (`customTools.ts`): steps are
 * derived from the active tools in system-decided dependency order, so the
 * old checkbox/↑↓ pipeline-editor helpers were removed.
 */
export type CustomStepId = StageKey;

export type CustomStep = {
  id: CustomStepId;
  label: string;
  enabled: boolean;
  /** Per-step config (audio mode / logo region) fed to the job params. */
  config?: StepConfig;
};
