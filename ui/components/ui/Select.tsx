"use client";

import { ChevronDown } from "lucide-react";
import { forwardRef } from "react";
import styles from "./Select.module.css";

interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
	label?: string;
	options: { value: string; label: string }[];
	placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
	({ label, options, placeholder, className, ...props }, ref) => {
		return (
			<div>
				{label && <span className={styles.label}>{label}</span>}
				<div className={styles.wrapper}>
					<select ref={ref} className={`${styles.select} ${className ?? ""}`} {...props}>
						{placeholder && (
							<option value="" disabled>
								{placeholder}
							</option>
						)}
						{options.map((opt) => (
							<option key={opt.value} value={opt.value}>
								{opt.label}
							</option>
						))}
					</select>
					<ChevronDown className={styles.chevron} />
				</div>
			</div>
		);
	},
);

Select.displayName = "Select";
