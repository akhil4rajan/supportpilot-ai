import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, liveSocket, mergeMessage, wsUrl } from "../shared/http";
import { RichText } from "../shared/RichText";
import type { Conversation, ServerEvent } from "../shared/types";
import { api } from "./api";
import type { Detail, Session } from "./api";

const KEY = "supportpilot:console";
const TABS = [
  { id: "waiting_human", label: "Queue" },
  { id: "with_human", label: "Active" },
  { id: "resolved", label: "Resolved" },
] as const;

function loadSession(): Session | null {
  try { return JSON.parse(sessionStorage.getItem(KEY) ?? "null"); } catch { return null; }
}

export function App() {
  const [session, setSession] = useState<Session | null>(loadSession);
  const save = (s: Session | null) => {
    try { s ? sessionStorage.setItem(KEY, JSON.stringify(s)) : sessionStorage.removeItem(KEY); } catch { /* ignore */ }
    setSession(s);
  };
  return session ? <Dashboard session={session} onLogout={() => save(null)} /> : <Login onLogin={save} />;
}

function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [tenant, setTenant] = useState("acme");
  const [agent, setAgent] = useState("");
  const [key, setKey] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try { onLogin(await api.login(tenant.trim(), key, agent.trim())); }
    catch { setErr("Invalid credentials."); }
    finally { setBusy(false); }
  };
  return (
    <main className="login">
      <form onSubmit={submit}>
        <h1>Agent console</h1>
        <label>Company ID<input value={tenant} onChange={(e) => setTenant(e.target.value)} required autoFocus /></label>
        <label>Agent ID <small>(optional)</small><input value={agent} onChange={(e) => setAgent(e.target.value)} placeholder="sam" /></label>
        <label>Access key<input type="password" value={key} onChange={(e) => setKey(e.target.value)} required autoComplete="current-password" /></label>
        {err && <p className="err" role="alert">{err}</p>}
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}

const ago = (iso: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
};

function Dashboard({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("waiting_human");
  const [list, setList] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [live, setLive] = useState(false);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const tabRef = useRef(tab);
  tabRef.current = tab;
  const token = session.token;

  const guard = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) onLogout();
    else setError(e instanceof ApiError ? e.message : "Something went wrong.");
  }, [onLogout]);

  const refreshList = useCallback(async () => {
    try {
      const [cur, w, a] = await Promise.all([api.list(token, tabRef.current), api.list(token, "waiting_human"), api.list(token, "with_human")]);
      setList(cur.conversations);
      setCounts({ waiting_human: w.conversations.length, with_human: a.conversations.length });
    } catch (e) { guard(e); }
  }, [token, guard]);

  const refreshDetail = useCallback(async () => {
    const id = selectedRef.current;
    if (!id) return setDetail(null);
    try { setDetail(await api.get(token, id)); } catch (e) { guard(e); }
  }, [token, guard]);

  useEffect(() => { void refreshList(); }, [tab, refreshList]);
  useEffect(() => { void refreshDetail(); }, [selected, refreshDetail]);

  useEffect(() => liveSocket(
    () => wsUrl("", "/v1/agent/ws", token),
    (raw) => {
      const ev = raw as ServerEvent;
      void refreshList();
      if (ev.type === "message" && ev.conversation_id === selectedRef.current)
        setDetail((d) => (d ? { ...d, messages: mergeMessage(d.messages, ev.message) } : d));
      if (ev.type === "conversation_updated" && ev.conversation.id === selectedRef.current)
        setDetail((d) => (d ? { ...d, conversation: ev.conversation } : d));
      if (ev.type === "conversation_updated" && ev.conversation.status === "waiting_human") notify(ev.conversation);
    },
    () => { setLive(true); void refreshList(); void refreshDetail(); },
    onLogout,
  ), [token, refreshList, refreshDetail, onLogout]);

  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try { await fn(); await Promise.all([refreshList(), refreshDetail()]); } catch (e) { guard(e); }
  };

  const mine = detail?.conversation.assigned_agent === session.agent.name;
  return (
    <div className="shell">
      <aside>
        <div className="top">
          <div><b>{session.tenant.name}</b><small>{session.agent.name} · <span className={live ? "dot on" : "dot"} />{live ? "live" : "connecting"}</small></div>
          <button className="link" onClick={onLogout}>Sign out</button>
        </div>
        <nav role="tablist">
          {TABS.map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? "on" : ""} onClick={() => { setTab(t.id); setSelected(null); }}>
              {t.label}{counts[t.id] ? <span className="badge">{counts[t.id]}</span> : null}
            </button>
          ))}
        </nav>
        <ul className="list">
          {list.length === 0 && <li className="empty">Nothing here.</li>}
          {list.map((c) => (
            <li key={c.id}>
              <button className={c.id === selected ? "item on" : "item"} onClick={() => setSelected(c.id)}>
                <span className="row1"><b>{c.user.name ?? c.user.id ?? "Visitor"}</b><small>{ago(c.handoff_at ?? c.updated_at)}</small></span>
                <span className="reason">{c.handoff_reason ?? "—"}</span>
                <span className="prev">{c.last_message}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main>
        {!detail ? <div className="placeholder">Select a conversation</div> : (
          <ConversationView key={detail.conversation.id} detail={detail} mine={mine} error={error}
            onClaim={() => act(() => api.claim(token, detail.conversation.id))}
            onReply={(t) => act(() => api.reply(token, detail.conversation.id, t))}
            onResolve={() => act(() => api.resolve(token, detail.conversation.id))}
            onToBot={() => act(() => api.toBot(token, detail.conversation.id))} />
        )}
      </main>
    </div>
  );
}

function notify(c: Conversation) {
  try {
    if ("Notification" in window && Notification.permission === "granted") new Notification("Customer waiting", { body: c.handoff_reason ?? "A customer needs a human." });
    else if ("Notification" in window && Notification.permission === "default") void Notification.requestPermission();
  } catch { /* notifications unsupported */ }
}

function ConversationView(p: { detail: Detail; mine: boolean; error: string; onClaim: () => void; onReply: (t: string) => Promise<unknown> | void; onResolve: () => void; onToBot: () => void }) {
  const { conversation: c, messages, tickets } = p.detail;
  const [draft, setDraft] = useState("");
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [messages.length]);
  const inHandoff = c.status === "waiting_human" || c.status === "with_human";
  const canReply = inHandoff && (c.status === "waiting_human" || p.mine);
  const who = c.user.name ?? c.user.id ?? "Anonymous visitor";
  const send = (e: FormEvent) => { e.preventDefault(); if (!draft.trim()) return; void p.onReply(draft.trim()); setDraft(""); };
  const stamp = useMemo(() => new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }), []);

  return (
    <section className="conv">
      <header>
        <div>
          <h2>{who}</h2>
          <small>{c.user.authenticated ? `Verified customer${c.user.email ? " · " + c.user.email : ""}` : "Not signed in"} · <span className={`pill ${c.status}`}>{c.status.replace("_", " ")}</span>{c.assigned_agent && ` · ${c.assigned_agent}`}</small>
        </div>
        <div className="actions">
          {c.status === "waiting_human" && <button className="primary" onClick={p.onClaim}>Take over</button>}
          {inHandoff && p.mine && <button onClick={p.onToBot}>Return to bot</button>}
          {inHandoff && (p.mine || c.status === "waiting_human") && <button onClick={p.onResolve}>Resolve</button>}
        </div>
      </header>
      {(c.handoff_reason || c.handoff_summary) && (
        <div className="brief"><b>Why escalated:</b> {c.handoff_reason}<br /><b>Summary:</b> {c.handoff_summary}
          {tickets.length > 0 && <><br /><b>Tickets:</b> {tickets.map((t) => (t.url ? <a key={t.key} href={t.url} target="_blank" rel="noreferrer">{t.key}</a> : t.key)).reduce<React.ReactNode[]>((a, x, i) => (i ? [...a, ", ", x] : [x]), [])}</>}
        </div>
      )}
      <div className="msgs" ref={log} role="log">
        {messages.map((m) => m.role === "system"
          ? <div key={m.id} className="sys">{m.content}</div>
          : (
            <div key={m.id} className={`m ${m.role}`}>
              <small>{m.role === "user" ? who : m.role === "assistant" ? "Bot" : m.meta.agent ?? "Agent"} · {stamp.format(new Date(m.created_at))}{m.meta.tools?.length ? ` · used ${m.meta.tools.join(", ")}` : ""}</small>
              <div className="b"><RichText text={m.content} /></div>
            </div>
          ))}
      </div>
      {p.error && <p className="err" role="alert">{p.error}</p>}
      <form onSubmit={send}>
        <textarea value={draft} onChange={(e) => setDraft(e.target.value)} disabled={!canReply} rows={2}
          placeholder={canReply ? "Reply to the customer…" : c.status === "bot" ? "Bot is handling this conversation." : "Take over to reply."}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(e); } }} />
        <button className="primary" disabled={!canReply || !draft.trim()}>Send</button>
      </form>
    </section>
  );
}
