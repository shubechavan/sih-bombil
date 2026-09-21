"use client";

import { createElement, forwardRef, isValidElement } from "react";
import styles from "./Button.module.css";

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
	variant?: Variant;
	size?: Size;
	fullWidth?: boolean;
	loading?: boolean;
	icon?: React.ReactNode | React.ElementType;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
	(
		{
			variant = "primary",
			size = "md",
			fullWidth,
			loading,
			disabled,
			icon,
			children,
			className,
			type,
			...props
		},
		ref,
	) => {
		const classes = [
			styles.button,
			styles[variant],
			styles[size],
			fullWidth && styles.full,
			disabled && styles.disabled,
			loading && styles.loading,
			className,
		]
			.filter(Boolean)
			.join(" ");

		const iconNode = (() => {
			if (loading || icon == null) return null;
			if (isValidElement(icon)) return icon;

			if (typeof icon === "function" || (typeof icon === "object" && icon !== null)) {
				return createElement(
					icon as React.ElementType<{ size?: number; "aria-hidden"?: boolean }>,
					{
						size: 14,
						"aria-hidden": true,
					},
				);
			}

			return icon;
		})();

		return (
			// `type` defaults to "submit" in HTML, so any <Button> that ends up
			// inside a <form> submits it unless told otherwise. Default to
			// "button" and let a real submit opt in.
			<button
				ref={ref}
				type={type ?? "button"}
				className={classes}
				disabled={disabled || loading}
				{...props}
			>
				{loading && <span className={styles.spinner} />}
				{iconNode}
				{children}
			</button>
		);
	},
);

Button.displayName = "Button";
