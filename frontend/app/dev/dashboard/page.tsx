import { notFound } from "next/navigation";

import { DashboardPreview } from "@/components/dashboard/DashboardPreview";

/**
 * Dev-only dashboard fixture for visual iteration.
 * Renders the full dashboard with hand-crafted state. Returns 404 in
 * production builds so the route never ships to players.
 */
export default function DevDashboardPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <DashboardPreview />;
}
