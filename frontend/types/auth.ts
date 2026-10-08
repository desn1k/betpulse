// Subset of the backend UserOut we use on the client.
export interface AuthUser {
  id: string;
  email: string;
  role: "user" | "admin";
  totp_enabled: boolean;
  must_change_password: boolean;
  /** The server requires TOTP before this account's protected routes (F10). */
  two_factor_required: boolean;
}

export interface AccessTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}
