import SubtitleEditorView, { type SubtitleEditorViewProps } from "./SubtitleEditorView";

export default function ProjectPage({ projectId }: SubtitleEditorViewProps = {}) {
  return <SubtitleEditorView projectId={projectId} />;
}
