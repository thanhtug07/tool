import { useId } from "react";

export const WATERMARK_POSITIONS = [
  "top-left",
  "top",
  "top-right",
  "left",
  "center",
  "right",
  "bottom-left",
  "bottom",
  "bottom-right",
] as const;

export type WatermarkPosition = (typeof WATERMARK_POSITIONS)[number] | "custom";

export type WatermarkConfig = {
  kind: "none" | "text" | "image";
  text?: string;
  imagePath?: string;
  position: WatermarkPosition;
  /** Px distance from the named edge (ignored when position is custom). */
  margin: number;
  /** Custom x offset in px (used only when position is "custom"). */
  x: number;
  /** Custom y offset in px (used only when position is "custom"). */
  y: number;
  fontSize: number;
  color: string;
  opacity: number;
  rotation: number;
  font?: string;
  /** Target image width in px (0 keeps the original size). */
  imageWidth: number;
};

export const DEFAULT_WATERMARK: WatermarkConfig = {
  kind: "none",
  text: "",
  imagePath: "",
  position: "bottom-right",
  margin: 24,
  x: 0,
  y: 0,
  fontSize: 48,
  color: "#FFFFFFFF",
  opacity: 1,
  rotation: 0,
  font: "",
  imageWidth: 0,
};

/** A disabled form maps to a disabled watermark (nothing burns in). */
export type WatermarkDraft = WatermarkConfig;

export type WatermarkConfigProps = {
  value: WatermarkConfig;
  onChange: (next: WatermarkConfig) => void;
  /** Optional translated labels; defaults to Vietnamese. */
  labels?: Partial<
    Record<"kind" | "none" | "text" | "image" | "position" | "custom" | "margin", string>
  >;
};

function NumberField({
  id,
  label,
  value,
  min,
  max,
  step,
  onCommit,
}: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onCommit: (next: number) => void;
}) {
  const clamped = Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
  return (
    <label htmlFor={id} className="flex items-center justify-between gap-2 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <input
        id={id}
        type="number"
        className="w-24 rounded border border-input bg-background px-2 py-1"
        min={min}
        max={max}
        step={step}
        value={clamped}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isFinite(next)) onCommit(next);
        }}
      />
    </label>
  );
}

/**
 * Watermark configuration form (TASK-028). A controlled component mirroring the
 * worker `WatermarkConfig` data model: toggle text/image, 9 named positions +
 * custom x/y, plus text (font/size/color/opacity/rotation) and image
 * (path/width/opacity) options. Pure UI — it never invokes the render backend.
 */
export default function WatermarkConfig({ value, onChange, labels = {} }: WatermarkConfigProps) {
  const baseId = useId();
  const t = {
    kind: "Watermark",
    none: "None",
    text: "Text",
    image: "Image",
    position: "Position",
    custom: "Custom (x/y)",
    margin: "Margin (px)",
    ...labels,
  };

  function patch(patch: Partial<WatermarkConfig>) {
    onChange({ ...value, ...patch });
  }

  function enabled(kind: "none" | "text" | "image") {
    return value.kind === kind;
  }

  return (
    <fieldset data-role="watermark-config" className="space-y-3">
      <legend className="text-sm font-medium">{t.kind}</legend>

      <div className="flex items-center gap-2" role="radiogroup" aria-label={t.kind}>
        {(["none", "text", "image"] as const).map((kind) => (
          <label key={kind} className="flex cursor-pointer items-center gap-1.5 text-sm">
            <input
              type="radio"
              name={baseId}
              checked={enabled(kind)}
              onChange={() => patch({ kind })}
            />
            <span>{t[kind]}</span>
          </label>
        ))}
      </div>

      {value.kind !== "none" && (
        <>
          {value.kind === "text" && (
            <div className="space-y-2">
              <label htmlFor={`${baseId}-text`} className="block text-xs text-muted-foreground">
                Text
              </label>
              <input
                id={`${baseId}-text`}
                type="text"
                data-role="watermark-text"
                className="w-full rounded border border-input bg-background px-2 py-1 text-sm"
                value={value.text ?? ""}
                onChange={(event) => patch({ text: event.target.value })}
              />
              <div className="grid grid-cols-2 gap-2">
                <NumberField
                  id={`${baseId}-font-size`}
                  label="Font size"
                  value={value.fontSize}
                  min={1}
                  max={2048}
                  step={1}
                  onCommit={(next) => patch({ fontSize: next })}
                />
                <NumberField
                  id={`${baseId}-opacity`}
                  label="Opacity"
                  value={value.opacity}
                  min={0}
                  max={1}
                  step={0.05}
                  onCommit={(next) => patch({ opacity: next })}
                />
                <NumberField
                  id={`${baseId}-rotation`}
                  label="Rotation (°)"
                  value={value.rotation}
                  min={0}
                  max={360}
                  step={1}
                  onCommit={(next) => patch({ rotation: next })}
                />
                <label
                  htmlFor={`${baseId}-color`}
                  className="flex items-center justify-between gap-2 text-xs"
                >
                  <span className="text-muted-foreground">Color</span>
                  <input
                    id={`${baseId}-color`}
                    type="color"
                    className="h-7 w-10 cursor-pointer rounded border border-input"
                    value={normalizeHexColor(value.color)}
                    onChange={(event) => patch({ color: event.target.value + "FF" })}
                  />
                </label>
              </div>
            </div>
          )}

          {value.kind === "image" && (
            <div className="space-y-2">
              <label
                htmlFor={`${baseId}-image-path`}
                className="block text-xs text-muted-foreground"
              >
                Image path (PNG/JPG/WebP)
              </label>
              <input
                id={`${baseId}-image-path`}
                type="text"
                data-role="watermark-image-path"
                className="w-full rounded border border-input bg-background px-2 py-1 text-sm"
                value={value.imagePath ?? ""}
                onChange={(event) => patch({ imagePath: event.target.value })}
              />
              <NumberField
                id={`${baseId}-image-width`}
                label="Target width (px)"
                value={value.imageWidth}
                min={0}
                max={16384}
                step={1}
                onCommit={(next) => patch({ imageWidth: next })}
              />
            </div>
          )}

          <select
            aria-label={t.position}
            className="w-full rounded border border-input bg-background px-2 py-1 text-sm"
            value={value.position}
            onChange={(event) => patch({ position: event.target.value as WatermarkPosition })}
          >
            {WATERMARK_POSITIONS.map((position) => (
              <option key={position} value={position}>
                {position}
              </option>
            ))}
            <option value="custom">{t.custom}</option>
          </select>

          <div className="text-xs text-muted-foreground">{t.margin}</div>
          {value.position !== "custom" && (
            <NumberField
              id={`${baseId}-margin`}
              label={t.margin}
              value={value.margin}
              min={0}
              max={4096}
              step={1}
              onCommit={(next) => patch({ margin: next })}
            />
          )}

          {value.position === "custom" && (
            <div className="grid grid-cols-2 gap-2">
              <NumberField
                id={`${baseId}-x`}
                label="x (px)"
                value={value.x}
                min={0}
                max={16384}
                step={1}
                onCommit={(next) => patch({ x: next })}
              />
              <NumberField
                id={`${baseId}-y`}
                label="y (px)"
                value={value.y}
                min={0}
                max={16384}
                step={1}
                onCommit={(next) => patch({ y: next })}
              />
            </div>
          )}
        </>
      )}
    </fieldset>
  );
}

function normalizeHexColor(color: string): string {
  const match = /^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$/.exec(color);
  return match ? `#${match[1].toLowerCase()}` : "#ffffff";
}
