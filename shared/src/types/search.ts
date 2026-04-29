import type { Listing, ListingCondition } from "./listing";

export interface SearchFilters {
  brand?: string;
  category?: string;
  size?: string;
  condition?: ListingCondition;
  minPriceCents?: number;
  maxPriceCents?: number;
}

export interface SearchHit extends Listing {
  score: number;
}

export interface SearchFacets {
  brand: Record<string, number>;
  category: Record<string, number>;
  size: Record<string, number>;
  condition: Record<string, number>;
}

export interface SearchResult {
  hits: SearchHit[];
  facets: SearchFacets;
  page: number;
  totalPages: number;
  totalHits: number;
}
