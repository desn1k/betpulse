/**
 * Name of the 18+ confirmation cookie. The age gate sets it in the browser and
 * the root layout reads it on the server, so it lives in a module without
 * "use client": imported from a client module, a server component gets a
 * client reference instead of the string and never finds the cookie (F12).
 */
export const AGE_GATE_COOKIE = "bp_age_ok";
