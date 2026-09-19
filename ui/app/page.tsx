import { redirect } from "next/navigation";

/**
 * The console has one entry point: the actor list.
 *
 * v1's landing page and mission-control dashboard were built around the
 * page-centric threat model and were removed with the rest of that surface.
 * Rather than invent a new dashboard nobody asked for, `/` goes where the work
 * starts.
 */
export default function Home() {
	redirect("/actors");
}
