import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { destroy, init } from "../widget";
import { pickSite } from "./sites";
import "./demo.css";

// This page stands in for a customer's website. In production the user token comes from the
// CUSTOMER'S backend (see examples/host_token_server.py); here a dev-only endpoint plays that role.
const SITE = pickSite(window.location.search);
const SESSION_KEY = `supportpilot:${SITE.publicKey}:session`;

function Store() {
  const [uid, setUid] = useState(() => sessionStorage.getItem("demo-user") ?? SITE.users[1].id);
  const user = SITE.users.find((u) => u.id === uid) ?? SITE.users[0];

  useEffect(() => {
    document.title = `${SITE.brand} · SupportPilot demo`;
    sessionStorage.setItem("demo-user", uid);
    sessionStorage.removeItem(SESSION_KEY); // new identity -> new chat session
    init({
      publicKey: SITE.publicKey,
      apiUrl: "",
      getUserToken: user.id
        ? async () => {
            const r = await fetch("/v1/dev/user-token", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ tenant_id: SITE.tenant, user_id: user.id, name: user.name, email: user.email }),
            });
            return r.ok ? ((await r.json()) as { user_token: string }).user_token : null;
          }
        : undefined,
    });
    return destroy;
  }, [uid, user.id, user.name, user.email]);

  return (
    <div style={{ "--accent": SITE.accent } as React.CSSProperties}>
      <header className="bar">
        <b>{SITE.brand}</b>
        <label>
          Signed in as&nbsp;
          <select value={uid} onChange={(e) => setUid(e.target.value)}>
            {SITE.users.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
          </select>
        </label>
      </header>
      <main>
        <section className="hero">
          <h1>{SITE.title}</h1>
          <p>{SITE.tagline}</p>
        </section>
        <section className="try">
          <h2>Things to try</h2>
          <ul>{SITE.tryThese.map((t) => <li key={t}>{t}</li>)}</ul>
          <p className="agent">Human agent console: <a href="/console/" target="_blank">/console/</a> (company <code>{SITE.tenant}</code> · <code>{SITE.consoleAgent}</code> · <code>{SITE.consoleKey}</code>)</p>
        </section>
        {SITE.footnote && <p className="foot">{SITE.footnote}</p>}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><Store /></StrictMode>);
