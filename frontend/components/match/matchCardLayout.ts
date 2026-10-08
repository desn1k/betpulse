// Shared fixed height so the card and its skeleton occupy the exact same box —
// swapping one for the other causes zero layout shift (a hard requirement).
// Kept out of the "use client" MatchCard so a server component rendering the
// skeleton gets the class name, not a client reference.
export const MATCH_CARD_HEIGHT = "h-[208px]";
