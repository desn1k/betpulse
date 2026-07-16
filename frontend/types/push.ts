// Frontend contract for the push endpoints (Phase 11). Mirrors app/schemas/push.py.

export type PushChannel = "telegram" | "webpush";

export interface PushSubscriptionInfo {
  id: string;
  channel: PushChannel;
}

export interface SubscriptionsResponse {
  subscriptions: PushSubscriptionInfo[];
  telegram_connected: boolean;
}

export interface FollowsResponse {
  fixture_ids: string[];
}

export interface FollowResponse {
  fixture_id: string;
  following: boolean;
}

export interface VapidKeyResponse {
  public_key: string;
}

export interface TelegramLinkResponse {
  url: string;
  expires_at: string;
}
