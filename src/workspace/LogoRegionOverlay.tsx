import { useLayoutEffect, useRef, useState } from "react";

import type { WorkspaceContext } from "./types";

export type LogoRegion = { x: number; y: number; width: number; height: number };

/**
 * LOGO REGION OVERLAY — the amber selection rectangle drawn over the LARGE
 * preview while the Xóa logo config panel is open. Source-pixel coordinates
 * (x/y/w/h) map to the visible video content (the element letterboxes with
 * `object-fit: contain`, so the rectangle follows the actual frame, not the
 * black bars). Dragging updates the shared `ctx.logoRegion` state, which the
 * config panel's number inputs mirror live — one source of truth.
 */
export default function LogoRegionOverlay({ ctx }: { ctx: WorkspaceContext }) {
  const region = ctx.logoRegion.region;
  const meta = ctx.meta;
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState<{ w: number; h: number } | null>(null);
  const [drag, setDrag] = useState<null | {
    mode: "move" | "se" | "nw";
    startX: number;
    startY: number;
    orig: LogoRegion;
  }>(null);

  // Track the wrapper size so the %-mapped rectangle stays glued to the frame
  // while the window/panes resize.
  useLayoutEffect(() => {
    const el = overlayRef.current;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  if (!region || !meta || meta.width <= 0 || meta.height <= 0) return null;
  const vw = meta.width;
  const vh = meta.height;

  // Visible content rect of the `object-contain` video inside this wrapper.
  function contentRect() {
    if (!box || box.w <= 0 || box.h <= 0) return null;
    const scale = Math.min(box.w / vw, box.h / vh);
    const width = vw * scale;
    const height = vh * scale;
    return { left: (box.w - width) / 2, top: (box.h - height) / 2, width, height };
  }

  function toSource(clientX: number, clientY: number) {
    const el = overlayRef.current;
    const rect = contentRect();
    if (!el || !rect) return { px: 0, py: 0 };
    const origin = el.getBoundingClientRect();
    const px = Math.round(((clientX - origin.left - rect.left) / rect.width) * vw);
    const py = Math.round(((clientY - origin.top - rect.top) / rect.height) * vh);
    return { px: Math.max(0, Math.min(vw, px)), py: Math.max(0, Math.min(vh, py)) };
  }

  const rect = contentRect();
  const style: React.CSSProperties = rect
    ? {
        left: rect.left + (region.x / vw) * rect.width,
        top: rect.top + (region.y / vh) * rect.height,
        width: (region.width / vw) * rect.width,
        height: (region.height / vh) * rect.height,
      }
    : { left: 0, top: 0, width: 0, height: 0 };

  return (
    <div
      ref={overlayRef}
      data-role="logo-region-overlay"
      className="absolute inset-0 z-20"
      onPointerMove={(e) => {
        if (!drag) return;
        const { px, py } = toSource(e.clientX, e.clientY);
        if (drag.mode === "move") {
          const dx = px - drag.startX;
          const dy = py - drag.startY;
          ctx.logoRegion.setRegion({
            ...drag.orig,
            x: Math.max(0, Math.min(vw - drag.orig.width, drag.orig.x + dx)),
            y: Math.max(0, Math.min(vh - drag.orig.height, drag.orig.y + dy)),
          });
        } else if (drag.mode === "se") {
          ctx.logoRegion.setRegion({
            ...drag.orig,
            width: Math.max(1, Math.min(vw - drag.orig.x, px - drag.orig.x)),
            height: Math.max(1, Math.min(vh - drag.orig.y, py - drag.orig.y)),
          });
        } else if (drag.mode === "nw") {
          ctx.logoRegion.setRegion({
            x: Math.max(0, Math.min(drag.orig.x + drag.orig.width - 1, px)),
            y: Math.max(0, Math.min(drag.orig.y + drag.orig.height - 1, py)),
            width: Math.max(1, drag.orig.x + drag.orig.width - px),
            height: Math.max(1, drag.orig.y + drag.orig.height - py),
          });
        }
      }}
      onPointerUp={() => setDrag(null)}
      onPointerLeave={() => setDrag(null)}
    >
      {/* Help chip while configuring */}
      <span
        data-role="logo-region-hint"
        className="pointer-events-none absolute left-1/2 top-2 z-30 -translate-x-1/2 rounded bg-black/70 px-2 py-0.5 text-[10px] font-medium text-amber-300"
      >
        Drag to move · corner handles to resize
      </span>
      <div
        className="absolute border-2 border-amber-400"
        style={style}
        onPointerDown={(e) => {
          e.stopPropagation();
          const { px, py } = toSource(e.clientX, e.clientY);
          setDrag({ mode: "move", startX: px, startY: py, orig: { ...region } });
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        }}
      >
        <span
          className="absolute -bottom-1.5 -right-1.5 size-3 cursor-se-resize rounded-sm bg-amber-400"
          onPointerDown={(e) => {
            e.stopPropagation();
            const { px, py } = toSource(e.clientX, e.clientY);
            setDrag({ mode: "se", startX: px, startY: py, orig: { ...region } });
            (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          }}
        />
        <span
          className="absolute -left-1.5 -top-1.5 size-3 cursor-nw-resize rounded-sm bg-amber-400"
          onPointerDown={(e) => {
            e.stopPropagation();
            const { px, py } = toSource(e.clientX, e.clientY);
            setDrag({ mode: "nw", startX: px, startY: py, orig: { ...region } });
            (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          }}
        />
      </div>
    </div>
  );
}
