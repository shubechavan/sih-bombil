import { create } from "zustand";
import { bff } from "@/lib/api";
import type { AlertPayload, AlertState, BackendAlertPayload, N8nResponse } from "@/types/alert";
import type { Severity } from "@/types/threat";

interface AlertStore {
  history: AlertState[];
  isSending: boolean;

  sendAlert: (payload: AlertPayload) => Promise<N8nResponse>;
  testWebhook: () => Promise<N8nResponse>;
  clearHistory: () => void;
}

function severityScore(severity: Severity): number {
  switch (severity) {
    case "CRITICAL":
      return 0.9;
    case "HIGH":
      return 0.72;
    case "MEDIUM":
      return 0.45;
    default:
      return 0.2;
  }
}

function severityAction(severity: Severity): BackendAlertPayload["action"] {
  switch (severity) {
    case "CRITICAL":
    case "HIGH":
      return "auto_alert";
    case "MEDIUM":
      return "analyst_queue";
    default:
      return "log_only";
  }
}

export const useAlertStore = create<AlertStore>()((set) => ({
  history: [],
  isSending: false,

  sendAlert: async (payload) => {
    set({ isSending: true });
    const entry: AlertState = {
      id: crypto.randomUUID(),
      payload,
      status: "pending",
      timestamp: Date.now(),
    };
    set((s) => ({ history: [entry, ...s.history] }));

    try {
      const source = (payload.source || "manual").trim() || "manual";
      const backendPayload: BackendAlertPayload = {
        severity: payload.severity,
        category: "manual",
        r_score: severityScore(payload.severity),
        title: payload.message.trim(),
        engine: source,
        query: source,
        source_hash: "manual-ui",
        reasoning: payload.message.trim(),
        timestamp: payload.timestamp,
        action: severityAction(payload.severity),
      };

      const res = await bff.post("alerts/send", { json: backendPayload }).json<N8nResponse>();
      set((s) => ({
        history: s.history.map((h) =>
          h.id === entry.id ? { ...h, status: "sent" as const, response: res } : h
        ),
        isSending: false,
      }));
      return res;
    } catch (err) {
      set((s) => ({
        history: s.history.map((h) =>
          h.id === entry.id
            ? { ...h, status: "failed" as const, error: err instanceof Error ? err.message : "Failed" }
            : h
        ),
        isSending: false,
      }));
      throw err;
    }
  },

  testWebhook: async () => {
    set({ isSending: true });
    try {
      const res = await bff.post("alerts/test").json<N8nResponse>();
      set({ isSending: false });
      return res;
    } catch (err) {
      set({ isSending: false });
      throw err;
    }
  },

  clearHistory: () => set({ history: [] }),
}));
