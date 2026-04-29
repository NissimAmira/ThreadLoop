export type ListingCondition = "new" | "like_new" | "good" | "fair";
export type ListingStatus = "draft" | "active" | "sold" | "removed";

export interface ListingImage {
  id: string;
  position: number;
  url: string;
  width: number | null;
  height: number | null;
}

export interface ListingArAsset {
  glbLowUrl: string;
  glbHighUrl: string;
  processedAt: string | null;
}

export interface Listing {
  id: string;
  sellerId: string;
  title: string;
  description: string | null;
  brand: string | null;
  category: string;
  size: string | null;
  condition: ListingCondition;
  priceCents: number;
  currency: string;
  status: ListingStatus;
  images: ListingImage[];
  arAsset: ListingArAsset | null;
  createdAt: string;
  updatedAt: string;
}

export interface CreateListingInput {
  title: string;
  description?: string;
  brand?: string;
  category: string;
  size?: string;
  condition: ListingCondition;
  priceCents: number;
  currency?: string;
}
