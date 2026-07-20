import { defineConfig, devices } from "@playwright/test";

const port = 3100;
const externalBaseUrl = process.env.PLAYWRIGHT_BASE_URL;
const baseURL = externalBaseUrl ?? `http://127.0.0.1:${port}`;
const browserChannel = process.env.PLAYWRIGHT_CHANNEL;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [["line"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: externalBaseUrl
    ? undefined
    : {
        command: `npm run build && npm run start -- --hostname 127.0.0.1 --port ${port}`,
        url: `${baseURL}/api/health`,
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(browserChannel ? { channel: browserChannel } : {}),
      },
    },
  ],
});
