import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import { Widget } from "./Widget";
import { css } from "./styles";
import type { WidgetOptions } from "./useChat";

export type { WidgetOptions };

let host: HTMLElement | null = null;
let root: Root | null = null;

/** Mount the widget inside a Shadow DOM so host-page CSS can neither break it nor be broken by it. */
export function init(opts: Partial<WidgetOptions> & { publicKey: string; tokenUrl?: string }): void {
  destroy();
  const apiUrl = (opts.apiUrl ?? scriptOrigin() ?? "").replace(/\/$/, "");
  const getUserToken =
    opts.getUserToken ??
    (opts.tokenUrl
      ? async () => {
          const r = await fetch(opts.tokenUrl!, { credentials: "include" });
          if (!r.ok) return null; // not signed in on the host site -> anonymous chat
          const j = (await r.json()) as { user_token?: string };
          return j.user_token ?? null;
        }
      : undefined);

  host = document.createElement("div");
  host.id = "supportpilot-root";
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = css;
  const mount = document.createElement("div");
  shadow.append(style, mount);
  root = createRoot(mount);
  root.render(<Widget opts={{ publicKey: opts.publicKey, apiUrl, getUserToken, primaryColor: opts.primaryColor, position: opts.position }} />);
}

export function destroy(): void {
  root?.unmount();
  host?.remove();
  root = host = null;
}

function scriptOrigin(): string | null {
  const s = (document.currentScript as HTMLScriptElement | null) ?? document.querySelector<HTMLScriptElement>("script[data-public-key]");
  try { return s?.src ? new URL(s.src).origin : null; } catch { return null; }
}

// Zero-JS integration: <script src="https://support.example/widget.js" data-public-key="pk_..." data-token-url="/api/support-token" defer>
const tag = (document.currentScript as HTMLScriptElement | null) ?? document.querySelector<HTMLScriptElement>("script[data-public-key]");
if (tag?.dataset.publicKey) {
  const run = () => init({ publicKey: tag.dataset.publicKey!, tokenUrl: tag.dataset.tokenUrl, apiUrl: tag.dataset.apiUrl, primaryColor: tag.dataset.color, position: tag.dataset.position === "left" ? "left" : "right" });
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", run) : run();
}
