const nextConfig = {
	reactStrictMode: true,
	// Emits .next/standalone — a self-contained server.js plus only the
	// node_modules actually reached by the build, traced file by file. The
	// runtime image then carries that instead of the full dependency tree, which
	// is what made it 1.98 GB: playwright-core, typescript and the rest of
	// devDependencies were being copied in wholesale.
	output: "standalone",
	experimental: {
		optimizePackageImports: ["lucide-react", "recharts"],
	},
};

export default nextConfig;
