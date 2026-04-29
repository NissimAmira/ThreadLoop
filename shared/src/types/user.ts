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

export interface Session {
  accessToken: string;
  expiresAt: string;
  user: User;
}
