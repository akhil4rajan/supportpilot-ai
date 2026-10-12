import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type { Message } from "../shared/types";
import { RichText } from "../shared/RichText";
import { useChat } from "./useChat";
import type { WidgetOptions } from "./useChat";

function Bubble({ m, assistantName }: { m: Message; assistantName: string }) {
  if (m.role === "system") return <div className="sys" role="status">{m.content}</div>;
  const cls = m.role === "user" ? "user" : m.role === "agent" ? "agent" : "bot";
  const who = m.role === "agent" ? (m.meta.agent ?? "Support agent") : m.role === "assistant" ? assistantName : "";
  return (
    <div className={`row ${cls}${m.pending ? " pending" : ""}`}>
      {who && <div className="who">{who}</div>}
      <div className="bubble"><RichText text={m.content} /></div>
      {m.meta.tickets?.map((t) => (
        <div className="ticket" key={t.key}>
          Ticket <b>{t.key}</b> created{t.url && <> · <a href={t.url} target="_blank" rel="noopener noreferrer">view</a></>}
        </div>
      ))}
      {!!m.meta.sources?.length && (
        <div className="chips" aria-label="Sources">
          {m.meta.sources.slice(0, 3).map((s) => <span className="chip" key={s.source + s.title}>{s.title || s.source}</span>)}
        </div>
      )}
    </div>
  );
}

export function Widget({ opts }: { opts: WidgetOptions }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const chat = useChat(opts, open);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const cfg = chat.config;
  const color = opts.primaryColor ?? cfg?.branding.primary_color ?? "#4f46e5";
  const side = opts.position === "left" ? "left" : "right";
  const status = chat.conv?.status ?? "bot";
  const human = status === "waiting_human" || status === "with_human";

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [chat.messages, chat.busy, open]);
  useEffect(() => { if (open) inputRef.current?.focus(); }, [open, chat.connecting]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!draft.trim() || chat.busy) return;
    void chat.send(draft);
    setDraft("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
    if (e.key === "Escape") setOpen(false);
  };

  if (!cfg && !chat.error) return null; // wait for branding so the launcher never flashes unstyled
  if (!cfg) return null;

  const style = { "--p": color } as React.CSSProperties;
  if (!open) {
    return (
      <div className="sp" style={style}>
        <button className={`launcher ${side}`} aria-expanded="false" onClick={() => setOpen(true)}>
          <span aria-hidden>💬</span> {cfg.branding.launcher_label}
        </button>
      </div>
    );
  }

  return (
    <div className="sp" style={style}>
      <section className={`panel ${side}`} role="dialog" aria-label={cfg.branding.title}>
        <header>
          <div className="t">
            <b>{cfg.branding.title}</b>
            <span>{status === "with_human" ? `Chatting with ${chat.conv?.assigned_agent ?? "a human agent"}` : human ? "Waiting for a human agent" : `${cfg.assistant_name} · virtual assistant`}</span>
          </div>
          <button onClick={() => void chat.requestHuman()} disabled={human || !chat.conv} title="Hand this chat to a person">Talk to a human</button>
          <button onClick={() => setOpen(false)} aria-label="Close chat">✕</button>
        </header>
        {status === "waiting_human" && <div className="banner">A person will join shortly. You can keep adding details below.</div>}
        {status === "with_human" && <div className="banner live">● {chat.conv?.assigned_agent ?? "A human agent"} is on the chat</div>}
        {status === "resolved" && <div className="banner">This conversation was resolved. Send a message to start a new one.</div>}
        <div className="log" ref={logRef} role="log" aria-live="polite" aria-relevant="additions">
          <div className="row bot"><div className="who">{cfg.assistant_name}</div><div className="bubble">{cfg.branding.greeting}</div></div>
          {chat.connecting && <div className="sys">Connecting…</div>}
          {chat.messages.map((m) => <Bubble key={m.id} m={m} assistantName={cfg.assistant_name} />)}
          {chat.busy && !human && <div className="typing" aria-label={`${cfg.assistant_name} is typing`}><i /><i /><i /></div>}
        </div>
        {chat.error && <div className="err" role="alert">{chat.error}</div>}
        <form onSubmit={submit}>
          <textarea ref={inputRef} rows={1} value={draft} maxLength={4000} aria-label="Message" disabled={!chat.conv}
            placeholder={human ? "Message the support team…" : "Type your question…"} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} />
          <button type="submit" disabled={!draft.trim() || chat.busy || !chat.conv}>Send</button>
        </form>
        <div className="foot">AI can make mistakes. A human is one click away.</div>
      </section>
    </div>
  );
}
