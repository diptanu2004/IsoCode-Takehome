import { FileSpreadsheet } from "lucide-react";
import { EmptyState, PageHeader, useTitle } from "../components/ui";
import { useUploads } from "../lib/uploads";

export function Overview() {
  useTitle("Overview");
  const uploads = useUploads();
  const hasReports = uploads.data?.some((u) => u.status === "done");
  return (
    <>
      <PageHeader title="Overview" description="Money at risk across partners, by age." />
      <EmptyState icon={FileSpreadsheet} title={hasReports ? "Reports are loaded" : "No partner reports yet"}>
        {hasReports
          ? "The money-at-risk view by partner and age is still to come."
          : "Once settlement, COD remittance and freight files are uploaded, money at risk appears here by partner and age."}
      </EmptyState>
    </>
  );
}
