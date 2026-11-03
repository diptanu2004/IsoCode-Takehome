import { History } from "lucide-react";
import { can, useMe } from "../auth/session";
import { formatDateTime } from "../lib/format";
import { useActivate, useVersions } from "../lib/mapping";
import { Alert, Badge, Button } from "./ui";

const MADE_BY: Record<string, string> = {
  seed: "Set up at launch",
  rules: "Suggested by rules",
  ai: "Suggested by AI",
  manual: "Adjusted by hand",
};

/** Saved layouts for one report source, with rollback to an earlier one. */
export function MappingVersions({ sourceId }: { sourceId: number }) {
  const { data: me } = useMe();
  const versions = useVersions(sourceId);
  const activate = useActivate(sourceId);
  const canChange = can(me, "mapping.approve");

  if (versions.isPending) return <div className="h-24 animate-pulse rounded-xl bg-surface" />;
  if (versions.isError) return <Alert>Couldn't load the saved layouts. {versions.error.message}</Alert>;

  return (
    <div className="rounded-xl border border-line bg-surface">
      {activate.error && (
        <div className="p-3">
          <Alert>{activate.error.message}</Alert>
        </div>
      )}
      <ul className="divide-y divide-line">
        {versions.data.map((v) => (
          <li key={v.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0 text-sm">
              <div className="flex items-center gap-2">
                <span className="font-medium">Version {v.version}</span>
                {v.is_active && <Badge tone="gain">In use</Badge>}
              </div>
              <p className="mt-0.5 text-xs text-muted">
                {MADE_BY[v.suggested_by] ?? v.suggested_by}
                {v.approved_by && ` · approved by ${v.approved_by}`}
                {v.approved_at && ` · ${formatDateTime(v.approved_at)}`}
                {` · ${v.uploads} ${v.uploads === 1 ? "file" : "files"} loaded`}
              </p>
              <p className="mt-0.5 truncate text-xs text-faint" title={Object.values(v.columns).join(", ")}>
                Columns: {Object.values(v.columns).join(", ")}
              </p>
            </div>
            {!v.is_active && canChange && (
              <Button
                variant="secondary"
                className="shrink-0"
                loading={activate.isPending && activate.variables === v.id}
                onClick={() => activate.mutate(v.id)}
              >
                <History className="size-4" aria-hidden />
                Use this version
              </Button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
