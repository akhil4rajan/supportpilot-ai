export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function request<T>(base: string, path: string, opts: { method?: string; token?: string; body?: unknown } = {}): Promise<T> {
  const res = await fetch(base + path, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers: {
      ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(opts.token ? { Authorization: `Bearer ${opts.token}` } : {}),
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : detail;
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function wsUrl(base: string, path: string, token: string): string {
  const origin = base || window.location.origin;
  return origin.replace(/^http/, "ws") + path + `?token=${encodeURIComponent(token)}`;
}

/** Reconnecting WebSocket. `onOpen` fires on every (re)connect so callers can refetch missed state. */
export function liveSocket(url: () => string, onEvent: (e: unknown) => void, onOpen: () => void, onAuthLost?: () => void): () => void {
  let ws: WebSocket | null = null;
  let stopped = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let ping: ReturnType<typeof setInterval> | undefined;

  const connect = () => {
    ws = new WebSocket(url());
    ws.onopen = () => {
      attempt = 0;
      onOpen();
      ping = setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 25000);
    };
    ws.onmessage = (m) => {
      try { onEvent(JSON.parse(m.data)); } catch { /* ignore malformed frame */ }
    };
    ws.onclose = (ev) => {
      clearInterval(ping);
      if (stopped) return;
      if (ev.code === 4401) return onAuthLost?.();
      timer = setTimeout(connect, Math.min(15000, 500 * 2 ** attempt++));
    };
  };
  connect();
  return () => {
    stopped = true;
    clearTimeout(timer);
    clearInterval(ping);
    ws?.close();
  };
}

/** Insert/replace a message by id, drop its optimistic twin, keep order. */
export function mergeMessage<T extends { id: number; role: string; content: string }>(list: T[], incoming: T): T[] {
  if (list.some((m) => m.id === incoming.id)) return list.map((m) => (m.id === incoming.id ? incoming : m));
  const withoutTwin =
    incoming.role === "user"
      ? list.filter((m) => !((m as { pending?: boolean }).pending && m.role === "user" && m.content === incoming.content))
      : list;
  return [...withoutTwin, incoming].sort((a, b) => (a.id < 0 || b.id < 0 ? 0 : a.id - b.id));
}
