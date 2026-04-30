export type AuthProvider = "google" | "apple" | "facebook";

export interface User {
  id: string;
  provider: AuthProvider;
  email: string | null;
  emailVerified: boolean;
  displayName: string;
  avatarUrl: string | null;
  canSell: boolean;
  canPurchase: boolean;
  sellerRating: number | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * Result of `POST /api/auth/{provider}/callback` and `POST /api/auth/refresh`.
 *
 * Two shapes share one envelope, distinguished by `linkRequired`:
 *   - Happy path: `linkRequired === false` → `accessToken`, `expiresAt`,
 *     `user` are all present.
 *   - Pending-link: `linkRequired === true` → the email collided with an
 *     existing account from a different provider. The client must prompt
 *     the user to re-authenticate with `linkProvider` and submit `linkToken`
 *     to confirm the merge. `accessToken`/`user` are absent in this state.
 */
export type Session = AuthenticatedSession | PendingLinkSession;

export interface AuthenticatedSession {
  linkRequired: false;
  accessToken: string;
  expiresAt: string;
  user: User;
}

export interface PendingLinkSession {
  linkRequired: true;
  linkProvider: AuthProvider;
  linkToken: string;
}
