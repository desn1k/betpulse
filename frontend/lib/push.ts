// Browser-side push helpers (Phase 11): follow/unfollow a match, manage push
// subscriptions, and the Telegram deep-link flow. Web Push registration lives
// here too — it registers the service worker and subscribes with the server's
// VAPID key. All authenticated calls go through the same-origin proxy routes,
// which attach the bearer token.

import { ApiError } from "@/lib/api";
import { authHeader } from "@/lib/auth/store";
import type {
  FollowsResponse,
  SubscriptionsResponse,
  TelegramLinkResponse,
  VapidKeyResponse,
} from "@/types/push";

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { accept: "application/json", ...(init.headers ?? {}), ...authHeader() },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(`request failed: ${res.status}`, res.status, body);
  }
  return (res.status === 204 ? (undefined as T) : ((await res.json()) as T));
}

// --- follows ----------------------------------------------------------------

export async function fetchFollows(): Promise<string[]> {
  const data = await request<FollowsResponse>("/api/live/push/follows");
  return data.fixture_ids;
}

export function followMatch(fixtureId: string): Promise<void> {
  return request(`/api/live/push/follow/${encodeURIComponent(fixtureId)}`, { method: "PUT" });
}

export function unfollowMatch(fixtureId: string): Promise<void> {
  return request(`/api/live/push/follow/${encodeURIComponent(fixtureId)}`, { method: "DELETE" });
}

// --- subscriptions + telegram ----------------------------------------------

export function fetchSubscriptions(): Promise<SubscriptionsResponse> {
  return request<SubscriptionsResponse>("/api/push/subscriptions");
}

export function deleteSubscription(id: string): Promise<void> {
  return request(`/api/push/subscriptions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function createTelegramLink(): Promise<TelegramLinkResponse> {
  return request<TelegramLinkResponse>("/api/push/telegram/link", { method: "POST" });
}

export function disconnectTelegram(): Promise<void> {
  return request("/api/push/telegram", { method: "DELETE" });
}

// --- Web Push registration --------------------------------------------------

export function isWebPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const normalized = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(normalized);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

/**
 * Register the service worker, request permission, and subscribe to Web Push
 * with the server's VAPID key. The subscription is registered with the backend
 * so the push worker can reach this browser. Throws on unsupported / denied.
 */
export async function enableWebPush(): Promise<void> {
  if (!isWebPushSupported()) throw new Error("push_unsupported");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("push_denied");

  const registration = await navigator.serviceWorker.register("/sw.js");
  const ready = await navigator.serviceWorker.ready.catch(() => registration);

  const { public_key } = await request<VapidKeyResponse>("/api/push/vapid-public-key");
  const subscription = await ready.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(public_key),
  });

  const json = subscription.toJSON();
  await request("/api/live/push/subscribe", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      channel: "webpush",
      endpoint: subscription.endpoint,
      keys: json.keys ?? {},
    }),
  });
}

/** Best-effort: unsubscribe from the browser push manager (server row is removed
 * separately via deleteSubscription). */
export async function disableWebPush(): Promise<void> {
  if (!isWebPushSupported()) return;
  const registration = await navigator.serviceWorker.getRegistration();
  const subscription = await registration?.pushManager.getSubscription();
  await subscription?.unsubscribe();
}
