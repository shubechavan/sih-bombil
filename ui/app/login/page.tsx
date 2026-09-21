"use client";

import { Button } from "@/components/ui";
import { useAuthStore } from "@/stores/auth";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState } from "react";
import styles from "./login.module.css";

/**
 * Sign in.
 *
 * The password never leaves this form except in the POST body, and the token
 * that comes back never reaches this page at all — the route handler puts it in
 * an httpOnly cookie and returns only a name and a role.
 */
export default function LoginPage() {
	const router = useRouter();
	const { signIn, check, identity, checked, pending, error } = useAuthStore();
	const [username, setUsername] = useState("");
	const [password, setPassword] = useState("");
	const userId = useId();
	const passId = useId();

	useEffect(() => {
		if (!checked) check();
	}, [checked, check]);

	useEffect(() => {
		if (identity) router.replace("/actors");
	}, [identity, router]);

	async function submit(event: React.FormEvent) {
		event.preventDefault();
		if (await signIn(username, password)) router.replace("/actors");
	}

	return (
		<div className={styles.page}>
			<form className={styles.card} onSubmit={submit}>
				<div className={styles.brand}>
					<span className={styles.mark}>DARKSENTINEL</span>
					<span className={styles.sub}>Attribution console</span>
				</div>

				<p className={styles.lede}>
					Authorized investigative use only. Every action you take here is recorded against your
					name, including what you read.
				</p>

				<div className={styles.field}>
					<label htmlFor={userId}>Operator</label>
					<input
						id={userId}
						className={styles.input}
						value={username}
						onChange={(e) => setUsername(e.target.value)}
						autoComplete="username"
						required
					/>
				</div>

				<div className={styles.field}>
					<label htmlFor={passId}>Password</label>
					<input
						id={passId}
						className={styles.input}
						type="password"
						value={password}
						onChange={(e) => setPassword(e.target.value)}
						autoComplete="current-password"
						required
					/>
				</div>

				{error && (
					<p className={styles.error} role="alert">
						{error}
					</p>
				)}

				<Button type="submit" loading={pending} className={styles.submit}>
					Sign in
				</Button>
			</form>
		</div>
	);
}
