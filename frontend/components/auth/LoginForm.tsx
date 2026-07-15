"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { LoginError, useAuthStore } from "@/lib/auth/store";
import { matchKeys } from "@/lib/queries";

export function LoginForm({ onDone }: { onDone?: () => void }) {
  const t = useTranslations();
  const login = useAuthStore((s) => s.login);
  const pending = useAuthStore((s) => s.pending);
  const queryClient = useQueryClient();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await login(email, password);
      // Refetch matches so they reflect the now-authenticated tier.
      await queryClient.invalidateQueries({ queryKey: matchKeys.all });
      onDone?.();
    } catch (err) {
      setError(err instanceof LoginError ? t("auth.invalidCredentials") : t("auth.loginFailed"));
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label={t("auth.login")}>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-muted-strong">{t("auth.email")}</span>
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-md border border-border bg-surface px-3 py-2 outline-none focus:border-brand"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-muted-strong">{t("auth.password")}</span>
        <input
          type="password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-md border border-border bg-surface px-3 py-2 outline-none focus:border-brand"
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-live">
          {error}
        </p>
      )}
      <Button type="submit" disabled={pending}>
        {pending ? t("auth.signingIn") : t("auth.login")}
      </Button>
    </form>
  );
}
