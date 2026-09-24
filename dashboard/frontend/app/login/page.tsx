"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    setBusy(false);
    if (response.ok) router.replace("/");
    else setError(response.status === 401 ? "Wrong password." : "Login is unavailable.");
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4">
        <div>
          <h1 className="text-xl font-semibold text-[var(--text-primary)]">Simple Flow Channels</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">Enter the password to continue.</p>
        </div>

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
          autoComplete="current-password"
          aria-label="Password"
          className="h-11 w-full rounded-md border border-[var(--border)] bg-[var(--bg-primary)] px-3 text-sm text-[var(--text-primary)] outline-none focus:ring-2 focus:ring-sky-500"
        />

        {error && <p role="alert" className="text-sm text-[var(--error)]">{error}</p>}

        <button
          type="submit"
          disabled={busy || !password}
          className="h-11 w-full rounded-md bg-[var(--text-primary)] text-sm font-medium text-white disabled:opacity-40"
        >
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
