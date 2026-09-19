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
		<html lang="en" data-theme="dark">
			<head>
				<link
					href="https://fonts.googleapis.com/css2?family=Sora:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap"
					rel="stylesheet"
				/>
			</head>
			<body>
				<AppShell>{children}</AppShell>
			</body>
		</html>
	);
}
