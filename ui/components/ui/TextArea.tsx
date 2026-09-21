"use client";

import { forwardRef, useId } from "react";
import styles from "./TextArea.module.css";

interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
	label?: string;
	maxLength?: number;
	/**
	 * A length the content must reach to be usable, shown next to the counter.
	 * The engine refuses stylometry below 300 characters of masked prose, and a
	 * box that lets you paste 200 and then reports a refusal has wasted the
	 * paste. Advisory only — nothing here blocks submission, because the
	 * refusal itself is a finding worth seeing.
	 */
	minUseful?: number;
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
	({ label, maxLength, minUseful, value, className, id, ...rest }, ref) => {
		const generatedId = useId();
		const fieldId = id ?? generatedId;
		const len = typeof value === "string" ? value.length : 0;
		const short = minUseful != null && len > 0 && len < minUseful;

		return (
			<div className={styles.wrapper}>
				{/* A real <label htmlFor>, not a <span>: a span is not announced and
				    does not move focus to the field when clicked. */}
				{label && (
					<label className={styles.label} htmlFor={fieldId}>
						{label}
					</label>
				)}
				<textarea
					ref={ref}
					id={fieldId}
					className={`${styles.textarea} ${className ?? ""}`}
					value={value}
					maxLength={maxLength}
					{...rest}
				/>
				{(maxLength != null || minUseful != null) && (
					// polite, not assertive: this updates on every keystroke and an
					// assertive region would interrupt the typing it is describing.
					<span
						className={styles.charCount}
						data-short={short}
						data-over={maxLength != null && len > maxLength * 0.9}
						aria-live="polite"
					>
						{short
							? `${len} characters — ${minUseful - len} more before stylometry will run`
							: maxLength != null
								? `${len}/${maxLength}`
								: `${len} characters`}
					</span>
				)}
			</div>
		);
	},
);

TextArea.displayName = "TextArea";
