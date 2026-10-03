// BetPulse Web Push service worker (Phase 11).
//
// The server sends a "tickle" on a probability swing. When the tickle carries
// the fixture id we fetch the public latest-swing snapshot and render a
// notification from it; otherwise a generic text is shown. Clicking the
// notification focuses (or opens) the match page.
//
// The live numbers come from the in-play baseline (score and minute only, no
// team strength). Every text says so and never presents them as a model edge.
// Today the sender posts an empty body (no payload encryption), so the generic
// text is what users see; it follows the browser language (ru, else en).

/* global self, clients */

const TEXTS = {
  ru: {
    fallback:
      "Обновилась базовая in-play оценка матча, за которым вы следите (только счёт и минута, без силы команд).",
    home: "П1",
    updated: "оценка обновлена",
    note: "Базовая in-play модель: учитывает только счёт и минуту, без силы команд",
  },
  en: {
    fallback:
      "The baseline in-play estimate moved on a match you follow (score and minute only, not team strength).",
    home: "home win",
    updated: "estimate updated",
    note: "Baseline in-play model: uses only the score and the minute, not team strength",
  },
};

function texts() {
  const lang = (self.navigator && self.navigator.language) || "";
  return lang.toLowerCase().startsWith("ru") ? TEXTS.ru : TEXTS.en;
}

self.addEventListener("push", (event) => {
  event.waitUntil(handlePush(event));
});

async function handlePush(event) {
  let fixtureId = null;
  try {
    fixtureId = event.data ? event.data.text() : null;
  } catch {
    fixtureId = null;
  }

  const t = texts();
  let title = "BetPulse";
  let body = t.fallback;
  let url = "/";

  if (fixtureId) {
    url = `/matches/${fixtureId}`;
    try {
      const res = await fetch(
        `/api/live/push/latest/${encodeURIComponent(fixtureId)}`,
      );
      if (res.ok) {
        const data = await res.json();
        title = `${data.home_team} ${data.home_score}–${data.away_score} ${data.away_team}`;
        const home = data.probs?.["1x2"]?.home;
        body =
          typeof home === "number"
            ? `${data.minute}' · ${t.home} ${Math.round(home * 100)}% · ${t.note}`
            : `${data.minute}' · ${t.updated} · ${t.note}`;
      }
    } catch {
      // Fall back to the generic notification below.
    }
  }

  await self.registration.showNotification(title, {
    body,
    tag: fixtureId ?? "betpulse",
    data: { url },
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
