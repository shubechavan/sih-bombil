"use client";

import { forwardRef } from "react";
import styles from "./TextArea.module.css";

interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  maxLength?: number;
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  ({ label, maxLength, value, className, ...rest }, ref) => {
    const len = typeof value === "string" ? value.length : 0;

    return (
      <div className={styles.wrapper}>
        {label && <span className={styles.label}>{label}</span>}
        <textarea
          ref={ref}
          className={`${styles.textarea} ${className ?? ""}`}
          value={value}
          maxLength={maxLength}
          {...rest}
        />
        {maxLength != null && (
          <span className={styles.charCount} data-over={len > maxLength * 0.9}>
            {len}/{maxLength}
          </span>
        )}
      </div>
    );
  }
);

TextArea.displayName = "TextArea";
