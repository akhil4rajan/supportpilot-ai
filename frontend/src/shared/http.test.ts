import { describe, expect, it } from "vitest";
import { mergeMessage, wsUrl } from "./http";

const m = (id: number, role: string, content: string, pending = false) => ({ id, role, content, pending });

describe("mergeMessage", () => {
  it("replaces an optimistic user message with the confirmed one (no duplicate)", () => {
    const list = [m(1, "assistant", "hi"), m(-1, "user", "where is my order", true)];
    const out = mergeMessage(list, m(7, "user", "where is my order"));
    expect(out.map((x) => x.id)).toEqual([1, 7]);
  });
  it("ignores duplicates delivered both via REST and WebSocket", () => {
    const list = [m(5, "assistant", "a")];
    expect(mergeMessage(list, m(5, "assistant", "a"))).toHaveLength(1);
  });
  it("keeps other pending messages with different text", () => {
    const list = [m(-1, "user", "second", true)];
    expect(mergeMessage(list, m(9, "user", "first"))).toHaveLength(2);
  });
  it("orders confirmed messages by id", () => {
    expect(mergeMessage([m(3, "agent", "c")], m(2, "system", "b")).map((x) => x.id)).toEqual([2, 3]);
  });
});

describe("wsUrl", () => {
  it("converts http(s) to ws(s) and encodes the token", () => {
    expect(wsUrl("https://support.example", "/v1/widget/ws", "a b")).toBe("wss://support.example/v1/widget/ws?token=a%20b");
    expect(wsUrl("http://localhost:8000", "/x", "t")).toBe("ws://localhost:8000/x?token=t");
  });
});
