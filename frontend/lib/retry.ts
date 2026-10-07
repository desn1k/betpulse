import { ApiError } from "./api";

/** Retries after the first failure for an error that may be transient. */
export const MAX_RETRIES = 1;

/**
 * TanStack Query ``retry`` for every query (set in app/providers.tsx).
 *
 * A 4xx answer (401 included) is the same on a second try: the request or the
 * caller is wrong, a quota is spent, or a rate limit is hit — repeating it only
 * spends the caller's request budget. 5xx answers (502 when the backend is down)
 * and network errors may pass, so they get one more try.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < MAX_RETRIES;
}
