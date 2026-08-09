import { useCallback, useState } from "react";

import { ping, type PingError, type PingResult } from "@/api/bridge";
import { Button } from "@/components/ui/button";

type ConnectionState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "success"; result: PingResult }
  | { status: "error"; error: PingError };

export type { ConnectionState };

export default function SettingsPage() {
  const [connection, setConnection] = useState<ConnectionState>({ status: "idle" });

  const handleTestConnection = useCallback(async () => {
    setConnection({ status: "testing" });
    try {
      const result = await ping();
      setConnection({ status: "success", result });
    } catch (error) {
      setConnection({ status: "error", error: error as PingError });
    }
  }, []);

  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading" className="text-lg font-semibold">
        Settings
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Application, model, and GPU settings ship in a later task.
      </p>

      <div className="mt-6 space-y-6">
        <section aria-labelledby="about-subheading" className="space-y-2">
          <h2 id="about-subheading" className="text-sm font-medium">
            About
          </h2>
          <div className="flex items-center gap-3">
            <Button
              type="button"
              onClick={handleTestConnection}
              disabled={connection.status === "testing"}
            >
              Test connection
            </Button>
            <ConnectionStatus state={connection} />
          </div>
        </section>
      </div>
    </section>
  );
}

export function ConnectionStatus({ state }: { state: ConnectionState }) {
  if (state.status === "idle") {
    return <p className="text-sm text-muted-foreground">Not tested yet.</p>;
  }

  if (state.status === "testing") {
    return <p className="text-sm text-muted-foreground">Testing connection…</p>;
  }

  if (state.status === "success") {
    return (
      <p className="text-sm text-emerald-400">
        {state.result.response} ({state.result.latencyMs} ms)
      </p>
    );
  }

  return <p className="text-sm text-destructive">{state.error.message}</p>;
}
