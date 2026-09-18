"use client";

import { BandPill } from "@/components/attribution";
import { StatCard, TacticalPanel } from "@/components/tactical";
import { Button, Select } from "@/components/ui";
import { type ActorSummary, BANDS, type Band, api, formatDate } from "@/lib/attribution";
import {
	type ColumnDef,
	type SortingState,
	flexRender,
	getCoreRowModel,
	getSortedRowModel,
	useReactTable,
} from "@tanstack/react-table";
import { FilterX, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import styles from "./actors.module.css";

export default function ActorsPage() {
	const [actors, setActors] = useState<ActorSummary[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [band, setBand] = useState<string>("");
	const [category, setCategory] = useState<string>("");
	const [minPersonas, setMinPersonas] = useState<string>("1");
	const [sorting, setSorting] = useState<SortingState>([]);

	useEffect(() => {
		let cancelled = false;
		setLoading(true);
		api<ActorSummary[]>("/actors", {
			band: band || undefined,
			category: category || undefined,
			min_personas: minPersonas,
			limit: 500,
		})
			.then((rows) => {
				if (!cancelled) {
					setActors(rows);
					setError(null);
				}
			})
			.catch((err: Error) => !cancelled && setError(err.message))
			.finally(() => !cancelled && setLoading(false));
		return () => {
			cancelled = true;
		};
	}, [band, category, minPersonas]);

	const categories = useMemo(
		() =>
			Array.from(
				new Set(actors.map((a) => a.category).filter((c): c is string => Boolean(c))),
			).sort(),
		[actors],
	);

	const columns = useMemo<ColumnDef<ActorSummary>[]>(
		() => [
			{
				accessorKey: "label",
				header: "Actor",
				cell: ({ row }) => (
					<Link href={`/actors/${row.original.id}`} className={styles.link}>
						{row.original.label ?? `actor ${row.original.id}`}
					</Link>
				),
			},
			{
				accessorKey: "band",
				header: "Confidence",
				cell: ({ row }) => <BandPill band={row.original.band} score={row.original.confidence} />,
				sortingFn: (a, b) => (a.original.confidence ?? -1) - (b.original.confidence ?? -1),
			},
			{
				accessorKey: "persona_count",
				header: "Personas",
				cell: ({ row }) => (
					<span className={styles.handles}>
						<strong>{row.original.persona_count}</strong>
						<span className={styles.handleList}>{row.original.handles.join(", ")}</span>
					</span>
				),
			},
			{ accessorKey: "source_count", header: "Sources" },
			{ accessorKey: "identifier_count", header: "Identifiers" },
			{
				accessorKey: "category",
				header: "Category",
				cell: ({ row }) => row.original.category ?? "—",
			},
			{
				accessorKey: "first_seen",
				header: "First seen",
				cell: ({ row }) => formatDate(row.original.first_seen),
			},
			{
				accessorKey: "last_seen",
				header: "Last seen",
				cell: ({ row }) => formatDate(row.original.last_seen),
			},
		],
		[],
	);

	const table = useReactTable({
		data: actors,
		columns,
		state: { sorting },
		onSortingChange: setSorting,
		getCoreRowModel: getCoreRowModel(),
		getSortedRowModel: getSortedRowModel(),
	});

	const merged = actors.filter((a) => a.persona_count > 1);

	return (
		<div className={styles.page}>
			<header className={styles.header}>
				<h1 className={styles.title}>
					<Users size={20} /> Actors
				</h1>
				<p className={styles.subtitle}>
					Personas resolved into actors by <code>link.cluster</code>, over links at or above the
					PROBABLE floor. Confidence is the <strong>weakest</strong> link holding a cluster together
					— a cluster is a chain.
				</p>
			</header>

			<div className={styles.stats}>
				<StatCard label="Actors" value={String(actors.length)} />
				<StatCard label="Multi-persona" value={String(merged.length)} />
				<StatCard
					label="Personas covered"
					value={String(actors.reduce((sum, a) => sum + a.persona_count, 0))}
				/>
				<StatCard
					label="Cross-source"
					value={String(actors.filter((a) => a.source_count > 1).length)}
				/>
			</div>

			<TacticalPanel
				title="Attribution matrix"
				subtitle={`${actors.length} actor(s)`}
				loading={loading}
				headerRight={
					<div className={styles.filters}>
						<Select
							value={band}
							onChange={(e) => setBand(e.target.value)}
							aria-label="Filter by confidence band"
							options={[
								{ value: "", label: "All bands" },
								...BANDS.map((b: Band) => ({ value: b, label: b })),
							]}
						/>
						<Select
							value={category}
							onChange={(e) => setCategory(e.target.value)}
							aria-label="Filter by category"
							options={[
								{ value: "", label: "All categories" },
								...categories.map((c) => ({ value: c, label: c })),
							]}
						/>
						<Select
							value={minPersonas}
							onChange={(e) => setMinPersonas(e.target.value)}
							aria-label="Minimum personas"
							options={[
								{ value: "1", label: "Any size" },
								{ value: "2", label: "Merged only (2+)" },
								{ value: "3", label: "3 or more" },
							]}
						/>
						<Button
							variant="ghost"
							onClick={() => {
								setBand("");
								setCategory("");
								setMinPersonas("1");
							}}
						>
							<FilterX size={14} /> Reset
						</Button>
					</div>
				}
			>
				{error ? (
					<p className={styles.error}>{error}</p>
				) : (
					<div className={styles.tableWrap}>
						<table className={styles.table}>
							<thead>
								{table.getHeaderGroups().map((group) => (
									<tr key={group.id}>
										{group.headers.map((header) => (
											<th key={header.id} className={styles.th}>
												<button
													type="button"
													className={styles.sortButton}
													onClick={header.column.getToggleSortingHandler()}
												>
													{flexRender(header.column.columnDef.header, header.getContext())}
													{{ asc: " ▲", desc: " ▼" }[header.column.getIsSorted() as string] ?? ""}
												</button>
											</th>
										))}
									</tr>
								))}
							</thead>
							<tbody>
								{table.getRowModel().rows.map((row) => (
									<tr key={row.id} className={styles.tr}>
										{row.getVisibleCells().map((cell) => (
											<td key={cell.id} className={styles.td}>
												{flexRender(cell.column.columnDef.cell, cell.getContext())}
											</td>
										))}
									</tr>
								))}
							</tbody>
						</table>
						{actors.length === 0 && !loading && (
							<p className={styles.empty}>
								No actors. Run <code>python -m link.cluster --source db</code>, or trigger a scan
								from the Export page.
							</p>
						)}
					</div>
				)}
			</TacticalPanel>
		</div>
	);
}
