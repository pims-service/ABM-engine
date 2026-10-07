/** What `GET /api/v1/auth/me/` returns (see docs/api/openapi.yaml). */
export interface AuthUser {
  id: string;
  email: string;
  name: string;
  is_staff: boolean;
  last_login: string | null;
  created_at: string;
}

/** Body of the BFF `login` and `refresh` responses. The refresh token never appears here. */
export interface SessionTokens {
  access: string;
  /** Access token expiry, epoch seconds. */
  expires_at: number;
}
