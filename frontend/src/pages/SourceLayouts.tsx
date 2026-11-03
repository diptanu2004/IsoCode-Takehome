import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router";
import { MappingVersions } from "../components/MappingVersions";
import { PageHeader, useTitle } from "../components/ui";
import { useSources } from "../lib/uploads";

export function SourceLayouts() {
  const sourceId = Number(useParams().sourceId);
  const sources = useSources();
  const source = sources.data?.find((s) => s.id === sourceId);
  useTitle(source ? `${source.name} layouts` : "Layouts");

  return (
    <>
      <Link to="/uploads" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
        <ArrowLeft className="size-4" aria-hidden /> Uploads
      </Link>
      <PageHeader
        title={source ? `${source.name}: saved layouts` : "Saved layouts"}
        description="Each approved column mapping is kept. The one in use reads new files; switch back if a partner reverts its format."
      />
      <MappingVersions sourceId={sourceId} />
    </>
  );
}
