// Subset of the backend UserOut we use on the client.
export interface AuthUser {
  id: string;
  email: string;
  role: "user" | "admin";
}

export interface AccessTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}
