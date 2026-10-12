export const css = `
:host { all: initial; }
*, *::before, *::after { box-sizing: border-box; }
.sp { --p: #4f46e5; --bg: #fff; --fg: #111827; --muted: #6b7280; --line: #e5e7eb; --soft: #f3f4f6;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--fg); }
@media (prefers-color-scheme: dark) { .sp { --bg: #111827; --fg: #f3f4f6; --muted: #9ca3af; --line: #374151; --soft: #1f2937; } }
.launcher { position: fixed; bottom: 20px; z-index: 2147483000; display: flex; align-items: center; gap: 8px; border: 0; cursor: pointer;
  background: var(--p); color: #fff; padding: 12px 18px; border-radius: 999px; font: inherit; font-weight: 600; box-shadow: 0 6px 24px rgba(0,0,0,.25); }
.launcher:hover { filter: brightness(1.08); }
.right { right: 20px; } .left { left: 20px; }
.panel { position: fixed; bottom: 20px; z-index: 2147483001; width: 380px; max-width: calc(100vw - 24px); height: 600px; max-height: calc(100vh - 40px);
  display: flex; flex-direction: column; background: var(--bg); border: 1px solid var(--line); border-radius: 16px; overflow: hidden; box-shadow: 0 12px 48px rgba(0,0,0,.3); }
@media (max-width: 480px) { .panel { inset: 0; width: 100vw; max-width: none; height: 100dvh; max-height: none; border-radius: 0; } }
header { background: var(--p); color: #fff; padding: 12px 14px; display: flex; align-items: center; gap: 10px; }
header .t { flex: 1; min-width: 0; } header .t b { display: block; font-size: 15px; } header .t span { font-size: 12px; opacity: .9; }
header button { background: rgba(255,255,255,.18); color: #fff; border: 0; border-radius: 8px; padding: 6px 10px; font: inherit; font-size: 12px; cursor: pointer; }
header button:hover:not(:disabled) { background: rgba(255,255,255,.3); } header button:disabled { opacity: .5; cursor: default; }
button:focus-visible, textarea:focus-visible, a:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.banner { padding: 8px 14px; font-size: 12.5px; background: var(--soft); border-bottom: 1px solid var(--line); color: var(--muted); }
.banner.live { background: #ecfdf5; color: #065f46; } @media (prefers-color-scheme: dark) { .banner.live { background: #064e3b; color: #d1fae5; } }
.log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; scroll-behavior: smooth; }
.row { display: flex; flex-direction: column; max-width: 86%; } .row.user { align-self: flex-end; align-items: flex-end; } .row.bot, .row.agent { align-self: flex-start; }
.who { font-size: 11px; color: var(--muted); margin: 0 4px 2px; }
.bubble { padding: 9px 12px; border-radius: 14px; white-space: pre-wrap; overflow-wrap: anywhere; }
.user .bubble { background: var(--p); color: #fff; border-bottom-right-radius: 4px; } .user.pending .bubble { opacity: .6; }
.bot .bubble { background: var(--soft); border-bottom-left-radius: 4px; } .agent .bubble { background: #ecfdf5; color: #064e3b; border-bottom-left-radius: 4px; border: 1px solid #a7f3d0; }
.bubble a { color: inherit; text-decoration: underline; }
.sys { align-self: center; font-size: 12px; color: var(--muted); text-align: center; padding: 2px 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; } .chip { font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; }
.ticket { margin-top: 6px; border: 1px solid var(--line); border-left: 3px solid var(--p); border-radius: 8px; padding: 6px 10px; font-size: 12.5px; background: var(--bg); }
.typing { align-self: flex-start; background: var(--soft); border-radius: 14px; padding: 10px 14px; display: flex; gap: 4px; }
.typing i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: b 1.2s infinite; } .typing i:nth-child(2) { animation-delay: .15s; } .typing i:nth-child(3) { animation-delay: .3s; }
@keyframes b { 0%,60%,100% { transform: translateY(0); opacity: .5 } 30% { transform: translateY(-4px); opacity: 1 } }
@media (prefers-reduced-motion: reduce) { .typing i { animation: none; } .log { scroll-behavior: auto; } }
.err { margin: 0 14px 6px; padding: 6px 10px; border-radius: 8px; font-size: 12.5px; background: #fef2f2; color: #991b1b; }
form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--line); align-items: flex-end; }
textarea { flex: 1; resize: none; border: 1px solid var(--line); border-radius: 12px; padding: 9px 12px; font: inherit; color: var(--fg); background: var(--bg); max-height: 110px; }
textarea:focus-visible { outline: 2px solid var(--p); border-color: transparent; }
form button { background: var(--p); color: #fff; border: 0; border-radius: 12px; padding: 9px 14px; font: inherit; font-weight: 600; cursor: pointer; }
form button:disabled { opacity: .5; cursor: default; }
.foot { text-align: center; font-size: 10.5px; color: var(--muted); padding: 0 0 6px; }
`;
