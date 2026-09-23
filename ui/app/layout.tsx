import type { Metadata } from "next";
import "@/styles/tokens.css";
import "@/styles/globals.css";
import "@/styles/charts.css";
import { AppShell } from "./AppShell";

export const metadata: Metadata = {
	title: "DarkSentinel",
	description: "Dark Web Intelligence Platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
	return (
		// suppressHydrationWarning: the inline script below flips data-theme
		// before React hydrates, based on a choice saved in localStorage —
		// without this, hydration compares that against the "dark" server
		// markup and logs a false mismatch.
		<html lang="en" data-theme="dark" suppressHydrationWarning>
			<head>
				<link
					href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap"
					rel="stylesheet"
				/>
				{/* Runs before paint, so a saved light-theme choice never flashes
				    the dark theme first on reload. */}
				<script
					// biome-ignore lint/security/noDangerouslySetInnerHtml: static string, no user input
					dangerouslySetInnerHTML={{
						__html:
							"try{var t=localStorage.getItem('ds-theme');if(t==='light')document.documentElement.setAttribute('data-theme','light');}catch(e){}",
					}}
				/>
			</head>
			<body>
				<AppShell>{children}</AppShell>
			</body>
		</html>
	);
}
