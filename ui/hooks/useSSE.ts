"use client";

import { useEffect, useState, useRef, useCallback } from "react";

type SSEStatus = "connecting" | "open" | "closed";

export function useSSE<T>(url: string | null, onMessage: (data: T) => void) {
  const [status, setStatus] = useState<SSEStatus>("connecting");
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!url) {
      setStatus("closed");
      return;
    }

    setStatus("connecting");
    const source = new EventSource(url);

    source.onopen = () => setStatus("open");

    source.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as T;
        onMessageRef.current(data);
      } catch {
        // skip malformed messages
      }
    };

    source.onerror = () => {
      setStatus("closed");
      source.close();
    };

    return () => {
      source.close();
      setStatus("closed");
    };
  }, [url]);

  return status;
}
