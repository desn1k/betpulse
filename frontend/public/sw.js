// BetPulse Web Push service worker (Phase 11).
//
// The server sends a data-less "tickle" on a probability swing; the tickle
// carries the fixture id. We fetch the public latest-swing snapshot and render a
// notification from it — so no payload encryption is needed. Clicking the
// notification focuses (or opens) the match page.
//
// The live numbers come from the in-play baseline (score and minute only, no
// team strength). The text always carries the server's `note` saying so and
// never presents them as a model edge.

/* global self, clients */

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

  let title = "BetPulse";
  let body = "Обновилась базовая in-play оценка матча, за которым вы следите.";
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
        const note =
          typeof data.note === "string" && data.note ? ` · ${data.note}` : "";
        body =
          typeof home === "number"
            ? `${data.minute}' · П1 ${Math.round(home * 100)}%${note}`
            : `${data.minute}' · оценка обновлена${note}`;
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
