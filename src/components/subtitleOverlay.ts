import type { SubtitleCue } from "@/api/subtitle";

/**
 * ASS subtitle style mirroring the worker's `SubtitleService` defaults
 * (TASK-024 / TASK-026). The preview overlay must match the final libass
 * burn-in: position bottom-center within the safe area, font size/stroke/
 * shadow authored on the 1920x1080 PlayRes grid and scaled to the rendered
 * video height.
 *
 * Documented MVP deviations (ARCHITECTURE_DECISION.md §4 / TASK-026):
 * - ASS outlines are approximated with an 8-direction CSS text-shadow
 *   (stroke within ~1px, no blur).
 * - `videoHeightPx` is the element box height; letterboxed (object-fit)
 *   content may differ slightly from the burn-in scale.
 * - Timing sync is the `timeupdate` cadence (>= 250 ms), within the +/-50 ms
 *   tolerance noted in TASK-026.
 */

export const PLAY_RES_X = 1920;
export const PLAY_RES_Y = 1080;
export const ASS_MARGIN_V = 24;
export const ASS_MARGIN_LR = 10;

export type OverlayPosition = "bottom_center" | "top_center";

export type SubtitleOverlayStyle = {
  font: string;
  /** Font size in PlayRes pixels (44 at 1080p). */
  fontSizePlayRes: number;
  /** Outline width in PlayRes pixels. */
  strokePlayRes: number;
  /** Drop shadow in PlayRes pixels. */
  shadowPlayRes: number;
  position: OverlayPosition;
  bgBox: boolean;
};

/** ASS-default overlay style shared by the vi/zh/en presets. */
export const ASS_DEFAULT_STYLE: SubtitleOverlayStyle = {
  font: "Arial",
  fontSizePlayRes: 44,
  strokePlayRes: 2,
  shadowPlayRes: 1,
  position: "bottom_center",
  bgBox: false,
};

/** Scale a PlayRes pixel value to a rendered video height (H/1080). */
export function scalePlayRes(value: number, videoHeightPx: number): number {
  return (value * videoHeightPx) / PLAY_RES_Y;
}

/** One-decimal CSS pixel string (stable + testable). */
export function cssPx(value: number): string {
  return `${Math.round(value * 10) / 10}px`;
}

/** The cue whose `[start, end)` window contains `time`, else null. */
export function activeCue(cues: SubtitleCue[], time: number): SubtitleCue | null {
  if (time < 0) return null;
  for (let i = cues.length - 1; i >= 0; i--) {
    const cue = cues[i];
    if (cue.start <= time && time < cue.end) return cue;
  }
  return null;
}

/** Render-time CSS for the caption overlay given a measured video height. */
export function captionStyle(
  style: SubtitleOverlayStyle,
  videoHeightPx: number,
): React.CSSProperties {
  const fontSize = scalePlayRes(style.fontSizePlayRes, videoHeightPx);
  const stroke = scalePlayRes(style.strokePlayRes, videoHeightPx);
  const shadow = scalePlayRes(style.shadowPlayRes, videoHeightPx);
  const marginV = scalePlayRes(ASS_MARGIN_V, videoHeightPx);
  const marginLR = scalePlayRes(ASS_MARGIN_LR, videoHeightPx);

  const directions = [-1, 0, 1];
  const outlines: string[] = [];
  for (const dx of directions) {
    for (const dy of directions) {
      if (dx === 0 && dy === 0) continue;
      outlines.push(`${dx * stroke}px ${dy * stroke}px 0 #000`);
    }
  }
  if (shadow > 0) {
    outlines.push(`0 ${shadow}px ${shadow}px rgba(0, 0, 0, 0.5)`);
  }

  return {
    position: "absolute",
    left: cssPx(marginLR),
    right: cssPx(marginLR),
    bottom: style.position === "bottom_center" ? cssPx(marginV) : "auto",
    top: style.position === "top_center" ? cssPx(marginV) : "auto",
    textAlign: "center",
    color: "#fff",
    fontFamily: style.font,
    fontSize: cssPx(fontSize),
    lineHeight: 1.3,
    fontWeight: 400,
    textShadow: outlines.join(", "),
    backgroundColor: style.bgBox ? "rgba(0, 0, 0, 0.5)" : undefined,
    padding: style.bgBox ? `${cssPx(stroke)} ${cssPx(fontSize / 4)}` : undefined,
  };
}
