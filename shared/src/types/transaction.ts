export type TransactionStatus =
  | "pending"
  | "paid"
  | "shipped"
  | "delivered"
  | "disputed"
  | "refunded";

export interface Transaction {
  id: string;
  listingId: string;
  buyerId: string;
  sellerId: string;
  amountCents: number;
  currency: string;
  status: TransactionStatus;
  createdAt: string;
  updatedAt: string;
}
