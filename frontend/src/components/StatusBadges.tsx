import { CLAIM_STATUS_LABELS, STATUS_LABELS, type ClaimStatus, type ReconStatus } from "../lib/recon";
import { Badge, type Tone } from "./ui";

const RECON_TONES: Record<ReconStatus, Tone> = {
  return_not_received: "loss",
  unpaid_past_due: "loss",
  short_paid: "loss",
  freight_overcharged: "loss",
  overpaid: "warn",
  pending: "accent",
  within_tolerance: "neutral",
  settled: "gain",
  not_applicable: "neutral",
};

const RECON_HINTS: Partial<Record<ReconStatus, string>> = {
  pending: "Not yet due, or the partner's report for this period hasn't been uploaded",
  within_tolerance: "Off by ₹10 or less in total. Watched for a pattern, not claimed",
  overpaid: "The partner paid more than the rate card says. Check before it's clawed back",
};

export function ReconBadge({ status }: { status: ReconStatus }) {
  return (
    <Badge tone={RECON_TONES[status]} title={RECON_HINTS[status]}>
      {STATUS_LABELS[status]}
    </Badge>
  );
}

const CLAIM_TONES: Record<ClaimStatus, Tone> = {
  open: "warn",
  raised: "accent",
  partner_rejected: "loss",
  recovered: "gain",
  written_off: "neutral",
  auto_resolved: "gain",
};

export function ClaimBadge({ status }: { status: ClaimStatus }) {
  return <Badge tone={CLAIM_TONES[status]}>{CLAIM_STATUS_LABELS[status]}</Badge>;
}
