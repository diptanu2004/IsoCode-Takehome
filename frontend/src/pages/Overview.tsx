import { FileSpreadsheet } from "lucide-react";
import { EmptyState, PageHeader, useTitle } from "../components/ui";

export function Overview() {
  useTitle("Overview");
  return (
    <>
      <PageHeader title="Overview" description="Money at risk across partners, by age." />
      <EmptyState icon={FileSpreadsheet} title="No partner reports yet">
        Once settlement, COD remittance and freight files are uploaded, money at risk appears here by
        partner and age.
      </EmptyState>
    </>
  );
}
