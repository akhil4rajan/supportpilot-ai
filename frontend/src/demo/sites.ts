// Demo "customer websites". Each one is a tenant in backend/tenants/<id>/ and a set of sample signed-in users.
// Pick one with ?site=<key>; the default is the hospital.
export interface DemoUser { id: string; label: string; name?: string; email?: string }
export interface DemoSite {
  tenant: string; publicKey: string; brand: string; title: string; tagline: string; accent: string;
  users: DemoUser[]; tryThese: string[]; consoleKey: string; consoleAgent: string; footnote?: string;
}

export const SITES: Record<string, DemoSite> = {
  hospital: {
    tenant: "wattlebay", publicKey: "pk_wattlebay_demo", brand: "WATTLE BAY HOSPITAL", accent: "#0f766e",
    title: "Caring for Sydney's northern beaches",
    tagline: "A fictional hospital website. The chat button in the corner is the SupportPilot widget.",
    users: [
      { id: "", label: "Guest (not signed in)" },
      { id: "peter", label: "Peter (has APT-1001, APT-1002)", name: "Peter Walker", email: "peter@example.com" },
      { id: "mary", label: "Mary (has APT-2001)", name: "Mary Thompson", email: "mary@example.com" },
    ],
    tryThese: [
      "Visiting hours? Parking? Is the emergency department open?",
      "Where is appointment APT-1001? Ask as Peter, then as Mary.",
      "I want to talk to a person (hands off to the patient services team)",
    ],
    consoleAgent: "sam", consoleKey: "wb-sam-demo-key",
    footnote: "Wattle Bay Hospital is fictional: all people, appointments and policies are invented for this demo. The assistant gives hospital information only, never medical advice. In an emergency call 000.",
  },
  retail: {
    tenant: "acme", publicKey: "pk_acme_demo", brand: "ACME OUTFITTERS", accent: "#4f46e5",
    title: "Gear for every trail",
    tagline: "A demo storefront. The chat bubble in the corner is the SupportPilot widget.",
    users: [
      { id: "", label: "Guest (not signed in)" },
      { id: "alice", label: "Alice (owns ACM-1001, ACM-1002)", name: "Alice Johnson", email: "alice@example.com" },
      { id: "bob", label: "Bob (owns ACM-2001)", name: "Bob Smith", email: "bob@example.com" },
    ],
    tryThese: [
      "How long do refunds take? (answers with a source)",
      "Where is order ACM-1001? Ask as Alice, then as Bob.",
      "Please open a ticket: my jacket zipper broke",
      "I want to talk to a person (hands off to a human)",
    ],
    consoleAgent: "sam", consoleKey: "sam-demo-key",
  },
};

export function pickSite(search: string): DemoSite {
  const k = new URLSearchParams(search).get("site") ?? "hospital";
  return SITES[k === "acme" ? "retail" : k] ?? SITES.hospital;
}
