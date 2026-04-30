/**
 * Standard API error envelope. Mirrors `Error` in `shared/openapi.yaml`.
 */
export interface ApiError {
  code: string;
  message: string;
  requestId?: string;
}
