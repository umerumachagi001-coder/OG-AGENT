// Approval policy engine — runs inside the n8n Code node "Apply approval policy".
// Rules come from agent/policy.json (sent by the bridge in $json.policy), so ops can change limits
// without touching the workflow. Mirrors agent/policy.py exactly; n8n/test_policy_parity.py checks that.

const fmt = (n) => Math.round(Number(n) || 0).toLocaleString('en-US');

function evaluate(option, ctx, p) {
  const auto = p.auto_execute || {};
  const always = new Set(p.always_escalate || []);
  const esc = p.escalate_if || {};
  const reasons = [];
  const checks = [];
  const steps = option.steps || [];
  if (!steps.length) reasons.push('option has no executable steps');

  const tier = ctx.customer_tier;
  if ((esc.customer_tier || []).includes(tier)) reasons.push(`customer tier is ${tier}`);
  const value = ctx.order_value || 0;
  if ('order_value_above' in esc) {
    if (value > esc.order_value_above) reasons.push(`order value ₹${fmt(value)} > ₹${fmt(esc.order_value_above)}`);
    else checks.push(`order value ₹${fmt(value)} ≤ ₹${fmt(esc.order_value_above)}`);
  }
  const conf = ctx.confidence;
  if (conf !== undefined && conf !== null && 'confidence_below' in esc) {
    if (conf < esc.confidence_below) reasons.push(`confidence ${conf.toFixed(2)} < ${esc.confidence_below}`);
    else checks.push(`confidence ${conf.toFixed(2)} ≥ ${esc.confidence_below}`);
  }
  const fraud = ctx.fraud_score;
  if (fraud !== undefined && fraud !== null && 'fraud_score_above' in esc && fraud > esc.fraud_score_above) {
    reasons.push(`payment fraud score ${fraud.toFixed(2)} > ${esc.fraud_score_above}`);
  }
  if ((esc.option_risk || []).includes(option.risk)) reasons.push(`option risk is ${option.risk}`);

  const total = steps.reduce((t, s) => t + (Number(s.est_cost) || 0), 0);
  if ('max_option_cost' in p && total > p.max_option_cost) {
    reasons.push(`total cost ₹${fmt(total)} > ₹${fmt(p.max_option_cost)}`);
  }

  for (const s of steps) {
    const t = s.type;
    const cost = Number(s.est_cost) || 0;
    if (always.has(t)) { reasons.push(`${t} always needs approval`); continue; }
    if (!(t in auto)) { reasons.push(`${t} is not auto-executable`); continue; }
    const rule = auto[t] || {};
    const lim = rule.max_extra_cost ?? rule.max_cost;
    if (lim !== undefined && lim !== null) {
      if (cost > lim) reasons.push(`${t} cost ₹${fmt(cost)} > limit ₹${fmt(lim)}`);
      else checks.push(`${t} cost ₹${fmt(cost)} ≤ ₹${fmt(lim)}`);
    }
    if ('max_attempts' in rule) {
      const n = ctx.payment_attempts || 0;
      if (n >= rule.max_attempts) reasons.push(`already ${n} payment attempts (max ${rule.max_attempts})`);
      else checks.push(`${n} prior attempts < ${rule.max_attempts}`);
    }
    if ('fraud_score_below' in rule) {
      const f = ctx.fraud_score;
      if (f === undefined || f === null || f >= rule.fraud_score_below) reasons.push(`fraud score ${f ?? 'None'} ≥ ${rule.fraud_score_below}`);
      else checks.push(`fraud score ${f} < ${rule.fraud_score_below}`);
    }
  }
  return { auto: reasons.length === 0, reasons, checks };
}

// ---- n8n entry (ignored by the parity test) ----
if (typeof $input !== 'undefined') {
  const d = $input.first().json;
  const verdicts = d.options.map((o) => evaluate(o, d.context, d.policy));
  return [{ json: {
    exception_id: d.exception_id,
    options: d.options,
    context: d.context,
    containment: d.containment || [],
    verdicts,
    top_auto: verdicts[0].auto,
  } }];
}
if (typeof module !== 'undefined') module.exports = { evaluate };
