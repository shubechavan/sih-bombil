import { bff } from "@/lib/api";
import type { Threat, ThreatFilters, ThreatPageResponse } from "@/types/threat";
import { create } from "zustand";

interface ThreatStore {
	items: Threat[];
	filters: ThreatFilters;
	total: number;
	page: number;
	pages: number;
	isLoading: boolean;
	source: "backend" | "local";
	fallbackActive: boolean;
	fallbackReason?: string;

	setFilters: (filters: Partial<ThreatFilters>) => void;
	fetchPage: (page: number) => Promise<void>;
	exportCSV: () => Promise<void>;
}

const defaultFilters: ThreatFilters = {
	severity: [],
	search: null,
	category: null,
	engine: null,
	threatsOnly: false,
	obfuscatedOnly: false,
	limit: 50,
};

export const useThreatStore = create<ThreatStore>()((set, get) => ({
	items: [],
	filters: defaultFilters,
	total: 0,
	page: 1,
	pages: 1,
	isLoading: false,
	source: "backend",
	fallbackActive: false,
	fallbackReason: undefined,

	setFilters: (partial) => {
		set((s) => ({ filters: { ...s.filters, ...partial } }));
	},

	fetchPage: async (page) => {
		set({ isLoading: true });
		try {
			const { filters } = get();
			const params = new URLSearchParams();
			params.set("page", String(page));
			params.set("limit", String(filters.limit ?? 50));
			if (filters.severity?.length) params.set("severity", filters.severity.join(","));
			if (filters.search) params.set("search", filters.search);
			if (filters.category) params.set("category", filters.category);
			if (filters.engine) params.set("engine", filters.engine);
			if (filters.threatsOnly) params.set("threats_only", "true");
			if (filters.obfuscatedOnly) params.set("obfuscated_only", "true");

			const data = await bff.get(`threats?${params}`).json<ThreatPageResponse>();
			set({
				items: data.items,
				total: data.total,
				page: data.page,
				pages: data.pages,
				source: data.source,
				fallbackActive: Boolean(data.fallbackActive),
				fallbackReason: data.fallbackReason,
				isLoading: false,
			});
		} catch {
			set({
				isLoading: false,
				items: [],
				source: "backend",
				fallbackActive: false,
				fallbackReason: undefined,
			});
		}
	},

	exportCSV: async () => {
		const { filters } = get();
		const params = new URLSearchParams();
		if (filters.severity?.length) params.set("severity", filters.severity.join(","));
		if (filters.category) params.set("category", filters.category);
		if (filters.threatsOnly) params.set("threats_only", "true");

		const blob = await bff.get(`export/csv?${params}`).blob();
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = `threats-${Date.now()}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	},
}));
