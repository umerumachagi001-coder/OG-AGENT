// Plain-language names and Indian number formatting.

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const rupees = (n, exact = false) =>
  n == null ? "—" : "₹" + (exact && n % 1 ? inr2.format(n) : inr.format(Math.round(n)));

// ₹1,48,050 → "₹1.48 lakh" for the metrics line
export const rupeesShort = (n) => {
  if (n == null) return "—";
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} crore`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(2)} lakh`;
  return rupees(n);
};

export const ago = (iso) => {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};

export const shortDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) : "—";

export const DOMAIN = {
  inventory: "Stock",
  supplier: "Supplier",
  payment: "Payment",
  pricing: "Pricing",
  deadline: "Delivery date",
};

// tone → styling in components/ui.jsx. Every status is shown as text, never colour alone.
export const STATUS = {
  open: { label: "Detected", tone: "crit", next: "Agent will investigate" },
  investigating: { label: "Investigating", tone: "live", next: "Agent is gathering evidence" },
  awaiting_approval: { label: "Needs your decision", tone: "warn", next: "Approve or reject a fix" },
  auto_resolved: { label: "Fixed by agent", tone: "good", next: "No action needed" },
  resolved: { label: "Fixed after approval", tone: "good", next: "No action needed" },
  rejected: { label: "Closed, no action", tone: "muted", next: "No action needed" },
  failed: { label: "Agent stopped", tone: "crit", next: "Investigate again" },
};

export const OPEN_STATUSES = ["open", "investigating", "awaiting_approval", "failed"];

// Severity: shape + label, so it reads without colour.
export const SEVERITY = {
  critical: { label: "Critical", rank: 0, icon: "◆", cls: "text-crit" },
  high: { label: "High", rank: 1, icon: "▲", cls: "text-serious" },
  medium: { label: "Medium", rank: 2, icon: "●", cls: "text-warn" },
  low: { label: "Low", rank: 3, icon: "○", cls: "text-muted" },
};

export const dateTime = (iso) =>
  iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

export const dateTimeSec = (iso) =>
  iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";

export const dayDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" }) : "—";

// What the agent did, in the words an ops person would use.
export const TOOL = {
  get_order: "Opened the order",
  check_inventory: "Checked stock",
  get_supplier_status: "Checked the supplier",
  get_payment_status: "Checked the payment",
  get_pricing_rules: "Checked pricing rules",
  get_sla: "Checked the delivery promise",
  find_similar_cases: "Looked up past cases",
  reallocate_stock: "Moved the order to another warehouse",
  switch_supplier: "Switched supplier",
  retry_payment: "Retried the payment",
  hold_order: "Put the order on hold",
  release_order: "Released the hold",
  apply_price_correction: "Corrected the price",
  expedite_shipping: "Upgraded shipping",
  notify_customer: "Emailed the customer",
  undo: "Undid an earlier step",
};

export const STEP_PARAM = {
  from: "From warehouse",
  to: "To warehouse",
  po_id: "Purchase order",
  supplier_id: "New supplier",
  carrier: "Carrier",
  price: "New price (₹)",
  message: "Message",
  reason: "Reason",
  promo_codes: "Offer codes",
};

// For proposed (not yet done) steps.
export const TOOL_DO = {
  reallocate_stock: "Ship from another warehouse",
  switch_supplier: "Switch supplier",
  retry_payment: "Retry the payment",
  hold_order: "Put the order on hold",
  release_order: "Release the hold",
  apply_price_correction: "Correct the price",
  expedite_shipping: "Upgrade shipping",
  notify_customer: "Email the customer",
};

const CARRIER = { STANDARD: "standard ground", EXPRESS: "express air", NEXT_DAY: "next-day priority" };

export function stepDetail(s) {
  const p = s.params || {};
  switch (s.type) {
    case "reallocate_stock": return `${p.from} to ${p.to}`;
    case "switch_supplier": return `${p.po_id} goes to ${p.supplier_id}`;
    case "expedite_shipping": return CARRIER[p.carrier] || p.carrier;
    case "apply_price_correction":
      return `${rupees(p.price, true)}${p.promo_codes?.length ? ` with ${p.promo_codes.join(", ")}` : ""}`;
    case "hold_order": return p.reason ? p.reason.replaceAll("_", " ") : "";
    case "undo": return p.action ? (TOOL[p.action] || p.action).toLowerCase() : "";
    default: return "";
  }
}

// Policy reasons come from the engine with action codes in them; make them readable.
export const humanize = (text) =>
  (text || "").replace(/\b([a-z]+(?:_[a-z]+)+)\b/g, (m) => (TOOL_DO[m] ? TOOL_DO[m].toLowerCase() : m.replaceAll("_", " ")));
