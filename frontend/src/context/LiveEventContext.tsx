import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { API_BASE_URL, getAuthToken } from "../services/api";

/**
 * Payload shape for live SSE events flowing through the EventSource
 * /api/stream/market-live connection.
 */
export interface LiveEventPayload {
  event: string;
  data?: unknown;
  ts: string;
}

interface LiveEventContextValue {
  /** The most recent live event received (or null if none yet). */
  latestEvent: LiveEventPayload | null;
  /** True when the EventSource connection is open. */
  isConnected: boolean;
  /** Manually request a reconnection (e.g. after auth state change). */
  reconnect: () => void;
}

const LiveEventContext = createContext<LiveEventContextValue>({
  latestEvent: null,
  isConnected: false,
  reconnect: () => {},
});

/** Debounce window (ms) for duplicate events of the same type. */
const DEBOUNCE_MS = 2000;

/**
 * Hook to consume the latest live market event.
 * Components can watch `latestEvent.event` to decide what to refresh.
 */
export function useLiveEvent(): LiveEventContextValue {
  return useContext(LiveEventContext);
}

/** Event types that the frontend acts on. */
const RELEVANT_EVENTS = new Set([
  "market_prices_changed",
  "market_refreshed",
  "decision_log_changed",
  "connected",
  "forecasts_precomputed",
]);

interface LiveEventProviderProps {
  /** Auth state — connection is only opened when "authenticated". */
  authState: string;
  /** Callback for morning_briefing payloads (kept from legacy App.tsx). */
  onMorningBriefing?: (payload: { markdown?: string; date?: string }) => void;
  children: React.ReactNode;
}

export const LiveEventProvider: React.FC<LiveEventProviderProps> = ({
  authState,
  onMorningBriefing,
  children,
}) => {
  const [latestEvent, setLatestEvent] = useState<LiveEventPayload | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const aliveRef = useRef(true);
  const lastEventRef = useRef<Map<string, number>>(new Map());
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const close = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    setIsConnected(false);
  }, []);

  const connect = useCallback(() => {
    if (authState !== "authenticated") {
      close();
      return;
    }

    close(); // close any existing connection first

    const authToken = getAuthToken();
    const streamUrl = authToken
      ? `${API_BASE_URL}/api/stream/market-live?access_token=${encodeURIComponent(authToken)}`
      : `${API_BASE_URL}/api/stream/market-live`;

    const source = new EventSource(streamUrl);
    sourceRef.current = source;

    source.onopen = () => {
      if (!aliveRef.current) return;
      setIsConnected(true);
    };

    source.onerror = () => {
      if (!aliveRef.current) return;
      setIsConnected(false);
      // EventSource auto-reconnects, but add a fallback manual reconnect
      reconnectTimerRef.current = setTimeout(() => {
        if (aliveRef.current && sourceRef.current?.readyState === EventSource.CLOSED) {
          close();
          connect();
        }
      }, 5_000);
    };

    // ---- Generic handler for relevant market events ----
    const handleMarketEvent = (e: MessageEvent) => {
      if (!aliveRef.current) return;

      // Debounce duplicate events within DEBOUNCE_MS window
      const now = Date.now();
      const last = lastEventRef.current.get(e.type) ?? 0;
      if (now - last < DEBOUNCE_MS) return;
      lastEventRef.current.set(e.type, now);

      let data: unknown = null;
      try {
        data = JSON.parse(e.data);
      } catch {
        // non-JSON payload — keep data as raw string
        data = e.data;
      }

      const payload: LiveEventPayload = {
        event: e.type,
        data,
        ts: new Date().toISOString(),
      };
      setLatestEvent(payload);
    };

    // Attach listeners for relevant events
    RELEVANT_EVENTS.forEach((eventName) => {
      source.addEventListener(eventName, handleMarketEvent);
    });

    // Keep legacy morning_briefing handler if callback provided
    if (onMorningBriefing) {
      source.addEventListener("morning_briefing", (e: MessageEvent) => {
        if (!aliveRef.current) return;
        try {
          const payload = JSON.parse(e.data) as {
            markdown?: string;
            date?: string;
          };
          if (payload.markdown) {
            onMorningBriefing(payload);
          }
        } catch {
          // ignore malformed event
        }
      });
    }
  }, [authState, close, onMorningBriefing]);

  const reconnect = useCallback(() => {
    connect();
  }, [connect]);

  // Main effect: connect/disconnect based on auth state
  useEffect(() => {
    aliveRef.current = true;
    connect();
    return () => {
      aliveRef.current = false;
      close();
    };
  }, [connect, close]);

  return (
    <LiveEventContext.Provider value={{ latestEvent, isConnected, reconnect }}>
      {children}
    </LiveEventContext.Provider>
  );
};
