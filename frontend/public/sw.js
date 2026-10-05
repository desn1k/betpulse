// BetPulse Web Push service worker (Phase 11).
//
// The server sends a "tickle" on a probability swing and the worker shows a
// generic, localized notification (ru, else en). Clicking it focuses (or opens)
// the match page when the push names a fixture, else the home page.
//
// No live numbers are fetched: the public snapshot endpoint that used to back
// a richer text was removed (audit A3), because it served in-play
// probabilities to tiers that may not see them. It may return only together
// with an encrypted push payload (RFC 8291) and a tier check (HANDOFF §9f).
// Today the sender posts an empty body, so the push never names a fixture.
//
// The texts describe the in-play baseline honestly (score and minute only, no
// team strength) and never present it as a model edge.

/* global self, clients */

// Bump on every change to this file: browsers compare the bytes, and the
// install/activate handlers below make the new version take over open tabs.
const SW_VERSION = "2026-10-05-a3";

const TEXTS = {
  ru: {
    fallback:
      "Обновилась базовая in-play оценка матча, за которым вы следите (только счёт и минута, без силы команд).",
  },
  en: {
    fallback:
      "The baseline in-play estimate moved on a match you follow (score and minute only, not team strength).",
  },
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function texts() {
  const lang = (self.navigator && self.navigator.language) || "";
  return lang.toLowerCase().startsWith("ru") ? TEXTS.ru : TEXTS.en;
}

// Activate a new version at once instead of waiting until every tab using the
// old one is closed, and take control of the open pages.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  event.waitUntil(handlePush(event));
});

function fixtureIdOf(event) {
  try {
    const text = event.data ? event.data.text().trim() : "";
    // Only a fixture id may become part of a URL.
    return UUID.test(text) ? text : null;
  } catch {
    return null;
  }
}

async function handlePush(event) {
  const fixtureId = fixtureIdOf(event);
  await self.registration.showNotification("BetPulse", {
    body: texts().fallback,
    tag: fixtureId ?? "betpulse",
    data: {
      url: fixtureId ? `/matches/${fixtureId}` : "/",
      version: SW_VERSION,
    },
  });
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url ?? "/";
  event.waitUntil(openMatch(url));
});

async function openMatch(url) {
  const windowClients = await clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });
  for (const client of windowClients) {
    if (client.url.includes(url) && "focus" in client) return client.focus();
  }
  return clients.openWindow(url);
}
