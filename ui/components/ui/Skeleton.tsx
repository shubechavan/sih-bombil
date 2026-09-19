import styles from "./Skeleton.module.css";

type Variant = "text" | "heading" | "circle" | "card";

interface SkeletonProps {
	variant?: Variant;
	width?: string | number;
	height?: string | number;
	className?: string;
}

export function Skeleton({ variant = "text", width, height, className }: SkeletonProps) {
	return (
		<div
			className={`${styles.skeleton} ${styles[variant]} ${className ?? ""}`}
			style={{ width, height }}
			aria-hidden="true"
		/>
	);
}
