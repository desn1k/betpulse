"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { TotpCodeInput } from "@/components/auth/TotpCodeInput";
import { Button } from "@/components/ui/Button";
import {
  AccountError,
  disableTotp,
  enableTotp,
  setupTotp,
  waitMinutes,
  type TotpSetup,
} from "@/lib/auth/account";
import { useAuthStore } from "@/lib/auth/store";

import { QrCode } from "./QrCode";

/** "JBSWY3DP…" → "JBSW Y3DP …", easier to type by hand. */
function groupKey(secret: string): string {
  return secret.replace(/(.{4})(?=.)/g, "$1 ");
}

/**
 * TOTP setup (QR code + manual key + confirmation code) and turning it off.
 * The secret from setup lives only in this component's state: never in storage,
 * the URL or a log, and gone when the page is left. Leaving before the code is
 * confirmed leaves TOTP off (the server activates it only on enable), and a new
 * setup issues a new secret.
 */
export function TwoFactorSection() {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);
  const reloadUser = useAuthStore((s) => s.reloadUser);

  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  if (!user) return null;

  function explain(err: unknown) {
    if (err instanceof AccountError && err.status === 400) setError(t("auth.totpInvalid"));
    else if (err instanceof AccountError && err.status === 429)
      setError(t("auth.tooManyAttempts", { minutes: waitMinutes(err.retryAfterSeconds) }));
    else setError(t("security.failed"));
  }

  async function run(action: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (err) {
      explain(err);
    } finally {
      setBusy(false);
    }
  }

  const startSetup = () =>
    run(async () => {
      try {
        setSetup(await setupTotp());
        setCode("");
      } catch (err) {
        // Another tab enabled TOTP meanwhile (F11: setup is refused then).
        if (err instanceof AccountError && err.status === 409) {
          await reloadUser();
          return;
        }
        throw err;
      }
    });

  const confirm = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await enableTotp(code);
      setSetup(null);
      setCode("");
      await reloadUser();
    });
  };

  const turnOff = (e: React.FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await disableTotp(code);
      setCode("");
      await reloadUser();
    });
  };

  async function copyKey(secret: string) {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
    } catch {
      // Clipboard denied: the key stays on screen for manual entry.
    }
  }

  const errorLine = error && (
    <p role="alert" className="text-sm text-live">
      {error}
    </p>
  );

  if (user.totp_enabled) {
    return (
      <div className="flex flex-col gap-3">
        <p role="status" className="text-sm font-semibold text-brand-strong">
          {t("security.totpEnabled")}
        </p>
        {user.role === "admin" && (
          <Link href="/admin" className="text-sm font-semibold text-brand hover:underline">
            {t("security.adminPanel")}
          </Link>
        )}
        <form onSubmit={turnOff} className="flex flex-col gap-3">
          <p className="text-sm text-muted-strong">{t("security.totpDisableHint")}</p>
          <TotpCodeInput value={code} onChange={setCode} />
          {errorLine}
          <Button type="submit" variant="secondary" disabled={busy || code.length !== 6}>
            {t("security.totpDisable")}
          </Button>
        </form>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-strong">
        {user.two_factor_required ? t("security.totpRequiredForAdmin") : t("security.totpIntro")}
      </p>
      {setup === null ? (
        <>
          {user.must_change_password && (
            <p className="text-sm text-muted-strong">{t("security.totpAfterPassword")}</p>
          )}
          {errorLine}
          <Button onClick={startSetup} disabled={busy || user.must_change_password}>
            {t("security.totpSetup")}
          </Button>
        </>
      ) : (
        <form onSubmit={confirm} className="flex flex-col gap-3">
          <p className="text-sm text-muted-strong">{t("security.totpScan")}</p>
          <QrCode value={setup.provisioning_uri} label={t("security.totpQrLabel")} />
          <div className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-muted-strong">{t("security.totpKey")}</span>
            <div className="flex items-center gap-2">
              <code className="rounded bg-surface-muted px-2 py-1 font-mono">{groupKey(setup.secret)}</code>
              <Button type="button" size="sm" variant="ghost" onClick={() => void copyKey(setup.secret)}>
                {copied ? t("security.copied") : t("security.copy")}
              </Button>
            </div>
          </div>
          <TotpCodeInput value={code} onChange={setCode} />
          {errorLine}
          <Button type="submit" disabled={busy || code.length !== 6}>
            {t("security.totpConfirm")}
          </Button>
        </form>
      )}
    </div>
  );
}
