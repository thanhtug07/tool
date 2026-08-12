import { open as openDialog, type OpenDialogOptions } from "@tauri-apps/plugin-dialog";

import { isTauri } from "@/lib/env";

/**
 * Native file / directory picker.
 *
 * Outside the Tauri webview (browser dev server, the Preview tab, tests) the
 * dialog plugin has no backend — `open()` rejects with the cryptic
 * `TypeError: Cannot read properties of undefined (reading 'invoke')`. This
 * wrapper converts that into a clear, catchable error so the calling page can
 * show a meaningful message instead of a raw stack trace.
 */
export async function pickFile(options: OpenDialogOptions): Promise<string | string[] | null> {
  if (!isTauri()) {
    throw new Error(
      "Cannot open the file picker: this page is running outside the Tauri window. Run the app with `npm run tauri dev`.",
    );
  }
  try {
    return await openDialog(options);
  } catch (error) {
    throw new Error("The file picker failed to open.", { cause: error });
  }
}
