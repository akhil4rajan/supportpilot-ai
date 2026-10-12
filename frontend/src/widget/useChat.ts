import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, liveSocket, mergeMessage, request, wsUrl } from "../shared/http";
import type { Conversation, Message, ServerEvent, WidgetConfig } from "../shared/types";

export interface WidgetOptions {
  publicKey: string;
  apiUrl: string;
  /** Returns a short-lived token minted by YOUR backend that proves who the signed-in user is. Omit for anonymous chat. */
  getUserToken?: () => Promise<string | null | undefined>;
  primaryColor?: string;
  position?: "right" | "left";
}

interface SessionPayload { token: string; conversation: Conversation; messages: Message[]; config: WidgetConfig }
const storeKey = (k: string) => `supportpilot:${k}:session`;

function readStored(k: string): string | null {
  try { return sessionStorage.getItem(storeKey(k)); } catch { return null; }
}
function writeStored(k: string, v: string | null) {
  try { v ? sessionStorage.setItem(storeKey(k), v) : sessionStorage.removeItem(storeKey(k)); } catch { /* storage blocked */ }
}

export function useChat(opts: WidgetOptions, active: boolean) {
  const [config, setConfig] = useState<WidgetConfig | null>(null);
  const [conv, setConv] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const token = useRef<string | null>(null);
  const tmpId = useRef(-1);

  // Branding is public: fetch it right away so the launcher can be styled before the visitor opens the chat.
  useEffect(() => {
    request<WidgetConfig>(opts.apiUrl, `/v1/widget/config?public_key=${encodeURIComponent(opts.publicKey)}`)
      .then(setConfig)
      .catch(() => setError("Support is unavailable right now."));
  }, [opts.apiUrl, opts.publicKey]);

  const start = useCallback(async () => {
    setConnecting(true);
    setError(null);
    try {
      let userToken: string | null | undefined;
      try { userToken = await opts.getUserToken?.(); } catch { userToken = null; } // fall back to anonymous
      const s = await request<SessionPayload>(opts.apiUrl, "/v1/widget/sessions", {
        body: { public_key: opts.publicKey, user_token: userToken || undefined, resume_token: readStored(opts.publicKey) || undefined },
      });
      token.current = s.token;
      writeStored(opts.publicKey, s.token);
      setConfig(s.config);
      setConv(s.conversation);
      setMessages(s.messages);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 401 ? "We couldn't verify your sign-in. Please reload the page." : "Couldn't connect to support. Please try again.");
    } finally {
      setConnecting(false);
    }
  }, [opts]);

  // Open the session lazily - only once the visitor actually opens the chat.
  useEffect(() => {
    if (active && !token.current && !connecting) void start();
  }, [active, connecting, start]);

  const refetch = useCallback(async () => {
    if (!token.current) return;
    try {
      const r = await request<{ conversation: Conversation; messages: Message[] }>(opts.apiUrl, "/v1/widget/conversation", { token: token.current });
      setConv(r.conversation);
      setMessages(r.messages);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) { token.current = null; writeStored(opts.publicKey, null); void start(); }
    }
  }, [opts, start]);

  // Live channel: human-agent messages and status changes arrive here.
  const convId = conv?.id;
  useEffect(() => {
    if (!convId || !token.current) return;
    return liveSocket(
      () => wsUrl(opts.apiUrl, "/v1/widget/ws", token.current!),
      (raw) => {
        const ev = raw as ServerEvent;
        if (ev.type === "message") setMessages((m) => mergeMessage(m, ev.message));
        else if (ev.type === "conversation_updated") setConv(ev.conversation);
      },
      () => void refetch(),
      () => { token.current = null; writeStored(opts.publicKey, null); void start(); },
    );
  }, [convId, opts, refetch, start]);

  const send = useCallback(async (text: string) => {
    const content = text.trim();
    if (!content || !token.current || busy) return;
    const temp: Message = { id: tmpId.current--, role: "user", content, meta: {}, created_at: new Date().toISOString(), pending: true };
    setMessages((m) => [...m, temp]);
    setBusy(true);
    setError(null);
    try {
      const r = await request<{ messages: Message[]; conversation: Conversation }>(opts.apiUrl, "/v1/widget/messages", { token: token.current, body: { content } });
      setConv(r.conversation);
      setMessages((m) => r.messages.reduce(mergeMessage, m.filter((x) => x.id !== temp.id && !(x.pending && x.content === content))));
      // The user's own message was persisted server-side; make sure it is present exactly once.
      void refetch();
    } catch (e) {
      setMessages((m) => m.filter((x) => x.id !== temp.id));
      setError(e instanceof ApiError && e.status === 429 ? "You're sending messages too quickly - give it a moment." : e instanceof ApiError && e.status === 413 ? "That message is too long." : "Message not sent. Please try again.");
    } finally {
      setBusy(false);
    }
  }, [busy, opts, refetch]);

  const requestHuman = useCallback(async () => {
    if (!token.current) return;
    try {
      const r = await request<{ conversation: Conversation }>(opts.apiUrl, "/v1/widget/handoff", { token: token.current, method: "POST", body: {} });
      setConv(r.conversation);
      void refetch();
    } catch { setError("Couldn't reach a human right now. Please try again."); }
  }, [opts, refetch]);

  return { config, conv, messages, busy, error, connecting, send, requestHuman };
}
