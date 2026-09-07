import { ThreatDetailClient } from "./ThreatDetailClient";

interface Props {
  params: Promise<{ hash: string }>;
}

export default async function ThreatDetailPage({ params }: Props) {
  const { hash } = await params;
  return <ThreatDetailClient hash={hash} />;
}
