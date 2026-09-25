"use client";

import { Eye, EyeOff, Lock } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (response.ok) {
        router.replace("/");
        return;
      }
      setError(response.status === 401 ? "Wrong password." : "Login is unavailable.");
    } catch {
      // A dropped connection used to leave the button stuck on "Checking…".
      setError("Could not reach the server. Check your connection and try again.");
    }
    setBusy(false);
  }

  return (
    <main className="flex min-h-[100dvh] items-center justify-center bg-surface-secondary px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-5 rounded-xl border border-border bg-surface-primary p-6 shadow-sm"
      >
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-full bg-surface-secondary text-ink-primary">
            <Lock className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-xl font-semibold text-ink-primary">Simple Flow Channels</h1>
            <p className="mt-1 text-sm text-ink-muted">Enter the password to continue.</p>
          </div>
        </div>

        <div className="relative">
          <input
            type={visible ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="current-password"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            // Labels the phone keyboard's return key "Go", which submits the form.
            enterKeyHint="go"
            aria-label="Password"
            placeholder="Password"
            // text-base (16px) stops iOS Safari zooming the page on focus.
            className="h-12 w-full rounded-md border border-border bg-surface-primary pl-3 pr-12 text-base text-ink-primary outline-none placeholder:text-ink-muted focus:ring-2 focus:ring-sky-500"
          />
          <button
            type="button"
            onClick={() => setVisible((v) => !v)}
            aria-label={visible ? "Hide password" : "Show password"}
            className="absolute right-1 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-md text-ink-muted hover:text-ink-primary"
          >
            {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>

        {error && (
          <p role="alert" className="text-sm text-status-rejected">
            {error}
          </p>
        )}

        {/* The button used to paint with CSS variables that were never
            defined (--text-primary), so it rendered transparent with white
            text — invisible. It uses the app's real theme tokens now. */}
        <button
          type="submit"
          disabled={busy || !password}
          className="h-12 w-full rounded-md bg-ink-primary text-base font-medium text-surface-primary transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
