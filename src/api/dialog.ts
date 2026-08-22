export interface OpenDialogOptions {
  title?: string;
  directory?: boolean;
  multiple?: boolean;
  filters?: Array<{ name: string; extensions: string[] }>;
  defaultPath?: string;
}

/**
 * Web browser file picker fallback.
 */
export async function pickFile(options: OpenDialogOptions): Promise<string | string[] | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    if (options.multiple) input.multiple = true;
    if (options.filters && options.filters.length > 0) {
      const exts = options.filters.flatMap((f) => f.extensions.map((ext) => `.${ext}`));
      input.accept = exts.join(",");
    }

    input.onchange = () => {
      if (!input.files || input.files.length === 0) {
        resolve(null);
        return;
      }
      if (options.multiple) {
        const paths = Array.from(input.files).map((f) => f.name);
        resolve(paths);
      } else {
        resolve(input.files[0].name);
      }
    };

    input.oncancel = () => resolve(null);
    input.click();
  });
}
