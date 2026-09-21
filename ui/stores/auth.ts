import type { Identity } from "@/lib/attribution";
import { create } from "zustand";

/**
 * Who is signed in.
 *
 * Deliberately holds no token — there is none to hold. The credential lives in
 * an httpOnly cookie the route handlers own, so this store knows only a name, a
 * role and whether the session has been checked yet. That last flag matters:
 * without it the shell cannot tell "not signed in" from "have not asked", and
 * would flash the login page at an operator who is already signed in.
 *
 * No `persist` middleware. The cookie is the source of truth and it can expire
 * without telling us; a cached identity in localStorage would outlive it and
 * present a session that no longer exists.
 */

interface AuthStore {
	identity: Identity | null;
	checked: boolean;
	pending: boolean;
	error: string | null;
	check: () => Promise<void>;
	signIn: (username: string, password: string) => Promise<boolean>;
	signOut: () => Promise<void>;
}

export const useAuthStore = create<AuthStore>()((set, get) => ({
	identity: null,
	checked: false,
	pending: false,
	error: null,

	check: async () => {
		try {
			const res = await fetch("/api/auth/me", { cache: "no-store" });
			set({ identity: res.ok ? await res.json() : null, checked: true });
		} catch {
			set({ identity: null, checked: true });
		}
	},

	signIn: async (username, password) => {
		if (get().pending) return false;
		set({ pending: true, error: null });
		try {
			const res = await fetch("/api/auth/login", {
				method: "POST",
				headers: { "content-type": "application/json" },
				body: JSON.stringify({ username, password }),
			});
			const payload = await res.json().catch(() => ({}));
			if (!res.ok) {
				set({ error: payload?.detail ?? "Sign-in failed", pending: false });
				return false;
			}
			// The login response carries a name and a role but not the token,
			// so re-ask /me for the full identity including what this role may do.
			await get().check();
			set({ pending: false });
			return true;
		} catch {
			set({ error: "Cannot reach the console API", pending: false });
			return false;
		}
	},

	signOut: async () => {
		try {
			await fetch("/api/auth/logout", { method: "POST" });
		} finally {
			set({ identity: null, checked: true, error: null });
		}
	},
}));
