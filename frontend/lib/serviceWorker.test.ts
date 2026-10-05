// @vitest-environment node
// Behaviour of public/sw.js, run in a fake service-worker scope: the generic
// notification, the click target, the take-over of open tabs by a new version,
// and the removed latest-swing snapshot (audit A3) staying removed.
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { beforeEach, describe, expect, it, vi } from "vitest";

const SW_PATH = fileURLToPath(new URL("../public/sw.js", import.meta.url));
const SOURCE = readFileSync(SW_PATH, "utf8");
const FIXTURE = "3f2b8c1e-9a4d-4e7f-b1c2-5d6e7f8a9b0c";

type Handler = (event: Record<string, unknown>) => void;
type NotificationShown = { body: string; tag: string; data: { url: string } };

function loadWorker(language = "en-GB") {
  const handlers: Record<string, Handler> = {};
  const showNotification = vi.fn<
    (title: string, options: NotificationShown) => Promise<void>
  >(async () => undefined);
  const focus = vi.fn(async () => undefined);
  const clients = {
    claim: vi.fn(async () => undefined),
    matchAll: vi.fn(
      async () => [] as Array<{ url: string; focus: typeof focus }>,
    ),
    openWindow: vi.fn(async () => undefined),
  };
  const self = {
    navigator: { language },
    registration: { showNotification },
    clients,
    skipWaiting: vi.fn(async () => undefined),
    addEventListener: (type: string, handler: Handler) => {
      handlers[type] = handler;
    },
  };
  const fetchSpy = vi.fn();
  new Function("self", "clients", "fetch", SOURCE)(self, clients, fetchSpy);

  async function dispatch(type: string, event: Record<string, unknown>) {
    const pending: Promise<unknown>[] = [];
    handlers[type]({
      ...event,
      waitUntil: (p: Promise<unknown>) => pending.push(p),
    });
    await Promise.all(pending);
  }
  return { self, clients, showNotification, focus, fetchSpy, dispatch };
}

const push = (text: string | null) => ({
  data: text === null ? null : { text: () => text },
});

describe("service worker", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows the generic English notification for an empty tickle", async () => {
    const sw = loadWorker("en-US");
    await sw.dispatch("push", push(null));
    expect(sw.showNotification).toHaveBeenCalledTimes(1);
    const [title, options] = sw.showNotification.mock.calls[0];
    expect(title).toBe("BetPulse");
    expect(options.body).toContain("baseline in-play estimate");
    expect(options.body).toContain("not team strength");
    expect(options.tag).toBe("betpulse");
    expect(options.data.url).toBe("/");
    expect(sw.fetchSpy).not.toHaveBeenCalled();
  });

  it("follows the browser language (ru)", async () => {
    const sw = loadWorker("ru-RU");
    await sw.dispatch("push", push(null));
    const options = sw.showNotification.mock.calls[0][1];
    expect(options.body).toContain("без силы команд");
  });

  it("targets the match page when the push names a fixture, without fetching", async () => {
    const sw = loadWorker();
    await sw.dispatch("push", push(FIXTURE));
    const options = sw.showNotification.mock.calls[0][1];
    expect(options.data.url).toBe(`/matches/${FIXTURE}`);
    expect(options.tag).toBe(FIXTURE);
    expect(sw.fetchSpy).not.toHaveBeenCalled();
  });

  it.each(["../admin", "/admin", "javascript:alert(1)", `${FIXTURE}/../x`])(
    "never puts a non-id payload into the URL: %s",
    async (payload) => {
      const sw = loadWorker();
      await sw.dispatch("push", push(payload));
      const options = sw.showNotification.mock.calls[0][1];
      expect(options.data.url).toBe("/");
    },
  );

  it("opens the target page on click, or focuses a tab already showing it", async () => {
    const sw = loadWorker();
    const close = vi.fn();
    const url = `/matches/${FIXTURE}`;
    await sw.dispatch("notificationclick", {
      notification: { close, data: { url } },
    });
    expect(close).toHaveBeenCalled();
    expect(sw.clients.openWindow).toHaveBeenCalledWith(url);

    sw.clients.matchAll.mockResolvedValueOnce([
      { url: `https://betpulse.app${url}`, focus: sw.focus },
    ]);
    await sw.dispatch("notificationclick", {
      notification: { close, data: { url } },
    });
    expect(sw.focus).toHaveBeenCalled();
    expect(sw.clients.openWindow).toHaveBeenCalledTimes(1);
  });

  it("takes over open tabs as soon as a new version installs", async () => {
    const sw = loadWorker();
    await sw.dispatch("install", {});
    expect(sw.self.skipWaiting).toHaveBeenCalled();
    await sw.dispatch("activate", {});
    expect(sw.clients.claim).toHaveBeenCalled();
    expect(SOURCE).toMatch(/const SW_VERSION = "[^"]+";/);
  });

  it("no longer uses the removed latest-swing snapshot (audit A3)", () => {
    expect(SOURCE).not.toContain("/api/live/push/latest");
    const route = fileURLToPath(
      new URL("../app/api/live/push/latest", import.meta.url),
    );
    expect(existsSync(route)).toBe(false);
  });
});
