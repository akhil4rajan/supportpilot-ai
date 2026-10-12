import type { ReactNode } from "react";

const TOKEN = /(\*\*[^*]+\*\*|https?:\/\/[^\s<>)]+)/g;

/** Minimal, XSS-safe formatting: **bold** and auto-linked http(s) URLs. Never uses innerHTML. */
export function RichText({ text }: { text: string }) {
  const out: ReactNode[] = [];
  text.split(TOKEN).forEach((part, i) => {
    if (!part) return;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) out.push(<strong key={i}>{part.slice(2, -2)}</strong>);
    else if (/^https?:\/\//.test(part)) out.push(<a key={i} href={part} target="_blank" rel="noopener noreferrer nofollow">{part}</a>);
    else out.push(part);
  });
  return <>{out}</>;
}
