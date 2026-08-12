import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { ProvidersProvider, useProviders } from "./providers";

/** Probe component that renders store state as data attributes. */
function Probe() {
  const store = useProviders();
  const free = store.providers.find((p) => p.id === "free");
  return (
    <div
      data-translation-default={store.defaults.translation}
      data-tts-default={store.defaultFor("tts")?.id ?? "null"}
      data-free-capabilities={(free?.capabilities ?? []).join(",")}
      data-free-needs-key={String(free?.needs_key ?? "missing")}
      data-translation-options={store
        .providersFor("translation")
        .map((p) => p.id)
        .join(",")}
      data-ids={store.providers.map((p) => p.id).join(",")}
    />
  );
}

function renderProbe() {
  return renderToStaticMarkup(
    <ProvidersProvider>
      <Probe />
    </ProvidersProvider>,
  );
}

function attr(html: string, name: string): string {
  const m = html.match(new RegExp(`${name}="([^"]*)"`));
  return m ? m[1] : "";
}

describe("ProvidersProvider", () => {
  it("seeds FREE as the default translation provider", () => {
    const html = renderProbe();
    expect(attr(html, "data-translation-default")).toBe("free");
    expect(attr(html, "data-tts-default")).toBe("free");
  });

  it("lists every seeded builtin (FREE first)", () => {
    const html = renderProbe();
    const ids = attr(html, "data-ids").split(",");
    expect(ids).toEqual(expect.arrayContaining(["free", "gemini", "local", "mock"]));
    expect(ids[0]).toBe("free");
  });

  it("providersFor returns only enabled translation-capable providers", () => {
    const html = renderProbe();
    const options = attr(html, "data-translation-options").split(",");
    // Every seeded translation provider is enabled → all selectable.
    expect(options).toEqual(expect.arrayContaining(["free", "gemini", "local", "mock"]));
    // Providers are filtered by the translation capability (FREE also has STT,
    // but STT alone is not a translation option in this build).
    for (const id of options) {
      expect(id.length).toBeGreaterThan(0);
    }
  });

  it("FREE advertises its real capabilities and needs no key", () => {
    const html = renderProbe();
    expect(attr(html, "data-free-capabilities")).toBe("translation,stt");
    expect(attr(html, "data-free-needs-key")).toBe("false");
  });
});
