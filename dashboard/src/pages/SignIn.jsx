import { useState } from "react";
import { signIn } from "../auth";
import { Button } from "../components/ui";

// Shown instead of the dashboard when Supabase login is on and nobody is signed in.
// Accounts are created by the project owner in Supabase (Authentication → Users); there is no sign-up here.
export default function SignIn({ expired }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { await signIn(email.trim(), password); }
    catch (err) { setError(err.message); setBusy(false); }
  };

  return (
    <main className="canvas-grid flex min-h-full items-center justify-center bg-night px-4 py-12">
      <div className="w-full max-w-[420px]">
        <div className="mb-6 flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" className="size-9" aria-hidden>
            <path d="M16 4l10 4.5v7c0 6.5-4.3 11-10 13-5.7-2-10-6.5-10-13v-7z" fill="none" stroke="var(--color-signal)" strokeWidth="2.4" />
            <circle cx="16" cy="15.5" r="3.2" fill="var(--color-signal)" />
          </svg>
          <p className="font-cond text-[26px] font-bold leading-none">OrderGuard <span className="text-signal">AI</span></p>
        </div>
        <form onSubmit={submit} className="rounded-xl bg-deck p-6 ring-1 ring-line" aria-labelledby="signin-title">
          <h1 id="signin-title" className="font-cond text-[26px] font-bold">Sign in</h1>
          <p className="mt-1 text-[14px] text-muted">Use the account your team lead created for you.</p>
          {expired && !error && (
            <p role="status" className="mt-3 rounded-lg bg-warn-wash px-3 py-2 text-[14px] text-warn ring-1 ring-warn/40">
              Your session ended. Sign in again to continue.
            </p>
          )}
          <label htmlFor="email" className="mt-5 block text-[13px] text-muted">Email</label>
          <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)}
            disabled={busy} className="mt-1 w-full rounded-lg bg-night px-3 py-2.5 text-[15px] text-ink ring-1 ring-line focus:outline-none focus:ring-signal" />
          <label htmlFor="password" className="mt-4 block text-[13px] text-muted">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} disabled={busy}
            className="mt-1 w-full rounded-lg bg-night px-3 py-2.5 text-[15px] text-ink ring-1 ring-line focus:outline-none focus:ring-signal" />
          {error && <p role="alert" className="mt-3 text-[14px] text-crit">{error}</p>}
          <Button kind="primary" type="submit" disabled={busy} className="mt-5 w-full justify-center">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
        <p className="mt-4 text-center text-[12.5px] text-dim">Secured by Supabase Auth. No account? Ask your project owner.</p>
      </div>
    </main>
  );
}
