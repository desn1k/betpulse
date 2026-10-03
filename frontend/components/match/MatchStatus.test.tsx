import { describe, expect, it } from "vitest";

import { summaryFixture } from "@/test/fixtures";
import { renderWithProviders } from "@/test/test-utils";

import { MatchStatus } from "./MatchStatus";

describe("MatchStatus kickoff", () => {
  it("shows date and time when the kickoff time is known", () => {
    const { container } = renderWithProviders(
      <MatchStatus match={{ ...summaryFixture, status: "scheduled", kickoff_time_known: true }} />,
      { locale: "en" },
    );
    const time = container.querySelector("time");
    expect(time).toHaveAttribute("dateTime", summaryFixture.kickoff_at);
    expect(time?.textContent).toMatch(/\d{1,2}:\d{2}/);
  });

  it("shows only the date when the source gave no time", () => {
    const { container } = renderWithProviders(
      <MatchStatus
        match={{
          ...summaryFixture,
          status: "scheduled",
          kickoff_at: "2015-08-08T12:00:00+00:00",
          kickoff_time_known: false,
        }}
      />,
      { locale: "ru" },
    );
    const time = container.querySelector("time");
    expect(time).toHaveAttribute("dateTime", "2015-08-08");
    expect(time?.textContent).not.toMatch(/\d{1,2}:\d{2}/);
    expect(time?.textContent).toMatch(/8/);
  });
});
