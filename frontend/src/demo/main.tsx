import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { destroy, init } from "../widget";
import "./demo.css";

// This page stands in for a customer's website. In production the user token comes from the
// CUSTOMER'S backend (see examples/host_token_server.py); here a dev-only endpoint plays that role.
const TENANT = "acme";
const USERS = [
  { id: "", label: "Guest (not signed in)" },
  { id: "alice", label: "Alice (owns ACM-1001, ACM-1002)", name: "Alice Johnson", email: "alice@example.com" },
  { id: "bob", label: "Bob (owns ACM-2001)", name: "Bob Smith", email: "bob@example.com" },
];

function Store() {
  const [uid, setUid] = useState(() => sessionStorage.getItem("demo-user") ?? "alice");
  const user = USERS.find((u) => u.id === uid) ?? USERS[0];

  useEffect(() => {
    sessionStorage.setItem("demo-user", uid);
    sessionStorage.removeItem("supportpilot:pk_acme_demo:session"); // new identity -> new chat session
    init({
      publicKey: "pk_acme_demo",
      apiUrl: "",
      getUserToken: user.id
        ? async () => {
            const r = await fetch("/v1/dev/user-token", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ tenant_id: TENANT, user_id: user.id, name: user.name, email: user.email }),
            });
            return r.ok ? ((await r.json()) as { user_token: string }).user_token : null;
          }
        : undefined,
    });
    return destroy;
  }, [uid, user.id, user.name, user.email]);

  return (
    <>
      <header className="bar">
        <b>ACME OUTFITTERS</b>
        <label>
          Signed in as&nbsp;
          <select value={uid} onChange={(e) => setUid(e.target.value)}>
            {USERS.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
          </select>
        </label>
      </header>
      <main>
        <section className="hero">
          <h1>Gear for every trail</h1>
          <p>This is a demo storefront. The chat bubble in the corner is the SupportPilot widget.</p>
        </section>
        <section className="try">
          <h2>Things to try</h2>
          <ul>
            <li><q>How long do refunds take?</q> - answers from the knowledge base, with a source.</li>
            <li><q>Where is order ACM-1001?</q> as Alice, then as Bob - Bob can't see Alice's order.</li>
            <li><q>Please open a ticket: my jacket zipper broke</q> - files a support ticket.</li>
            <li><q>I want to talk to a person</q> - hands off to a human; reply from <a href="/console/" target="_blank">/console/</a> (acme · sam · <code>sam-demo-key</code>).</li>
          </ul>
        </section>
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><Store /></StrictMode>);
