export type Role = "user" | "assistant" | "agent" | "system";
export type ConvStatus = "bot" | "waiting_human" | "with_human" | "resolved";

export interface Source { source: string; title: string }
export interface TicketRef { key: string; url: string | null }

export interface Message {
  id: number; // negative = optimistic, not yet confirmed by the server
  role: Role;
  content: string;
  meta: { sources?: Source[]; tickets?: TicketRef[]; tools?: string[]; event?: string; agent?: string; error?: string };
  created_at: string;
  pending?: boolean;
}

export interface Conversation {
  id: string;
  status: ConvStatus;
  user: { id: string | null; name: string | null; email: string | null; authenticated: boolean };
  handoff_reason: string | null;
  handoff_summary: string | null;
  handoff_at: string | null;
  assigned_agent: string | null;
  created_at: string;
  updated_at: string;
  last_message?: string;
}

export interface WidgetConfig {
  tenant_name: string;
  assistant_name: string;
  branding: { title: string; greeting: string; primary_color: string; launcher_label: string };
  features: { orders: boolean; tickets: boolean };
}

export type ServerEvent =
  | { type: "message"; conversation_id: string; message: Message }
  | { type: "conversation_updated"; conversation: Conversation };
