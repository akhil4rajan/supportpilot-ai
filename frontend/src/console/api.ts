import { request } from "../shared/http";
import type { Conversation, Message, TicketRef } from "../shared/types";

export interface Session { token: string; agent: { id: string; name: string }; tenant: { id: string; name: string } }
export interface Detail { conversation: Conversation; messages: Message[]; tickets: (TicketRef & { summary: string })[] }

const BASE = ""; // same origin (Vite proxy in dev, backend serves the build in prod)

export const api = {
  login: (tenant_id: string, api_key: string, agent_id?: string) =>
    request<Session>(BASE, "/v1/agent/login", { body: { tenant_id, api_key, agent_id: agent_id || undefined } }),
  list: (t: string, status: string) => request<{ conversations: Conversation[] }>(BASE, `/v1/agent/conversations?status=${status}`, { token: t }),
  get: (t: string, id: string) => request<Detail>(BASE, `/v1/agent/conversations/${id}`, { token: t }),
  claim: (t: string, id: string) => request<{ conversation: Conversation }>(BASE, `/v1/agent/conversations/${id}/claim`, { token: t, method: "POST", body: {} }),
  reply: (t: string, id: string, content: string) => request<{ message: Message }>(BASE, `/v1/agent/conversations/${id}/messages`, { token: t, body: { content } }),
  resolve: (t: string, id: string) => request<{ conversation: Conversation }>(BASE, `/v1/agent/conversations/${id}/resolve`, { token: t, method: "POST", body: {} }),
  toBot: (t: string, id: string) => request<{ conversation: Conversation }>(BASE, `/v1/agent/conversations/${id}/return-to-bot`, { token: t, method: "POST", body: {} }),
};
