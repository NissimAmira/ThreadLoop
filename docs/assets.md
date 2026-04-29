# Assets — images and AR

User-uploaded media (clothing photos and `.glb` 3D models) is the heaviest
data ThreadLoop handles. The pipeline is engineered so the API never proxies
bytes and clients always see CDN-optimized variants.

## Images

### Upload flow

```
[Client]                        [API]                     [Object store]    [Worker]
   │ POST /listings/:id/images    │                            │              │
   │ { content_type, size }       │                            │              │
   │─────────────────────────────▶│                            │              │
   │                              │ generate presigned PUT URL │              │
   │                              │  + storage_key             │              │
   │      { upload_url, key }     │                            │              │
   │◀─────────────────────────────│                            │              │
   │ PUT (image bytes) ───────────────────────────────────────▶│              │
   │                              │                            │ S3 event ───▶│
   │                              │                            │              │ generate
   │                              │                            │              │ derivatives
   │                              │                            │              │ + EXIF strip
   │                              │  worker writes derivatives │◀─────────────│
   │                              │  back, marks listing ready │              │
```

### Derivative ladder

Generated in WebP/AVIF (with JPEG fallbacks for ancient browsers):

| Variant | Long edge | Use case |
| --- | --- | --- |
| `thumb` | 256 px | Search result thumbnails, chat previews |
| `card` | 640 px | Listing cards in the feed (mobile) |
| `detail` | 1280 px | Listing detail page (desktop) |
| `zoom` | 2048 px | Full-screen zoom viewer |

Clients use `srcset`:

```html
<img
  srcset="
    /cdn/abc123.thumb.webp  256w,
    /cdn/abc123.card.webp   640w,
    /cdn/abc123.detail.webp 1280w,
    /cdn/abc123.zoom.webp   2048w
  "
  sizes="(max-width: 640px) 100vw, 50vw"
  loading="lazy"
/>
```

### Validation

Before a listing flips from `draft` to `active`:
- Content-type sniffed (don't trust the client header).
- EXIF metadata stripped (privacy + size).
- Dimensions validated (min 600×600, max 8000×8000).
- Virus scan (ClamAV in the worker).
- Reject animated images (we want stills).

### Caching

- CDN with **long TTL + immutable** (`Cache-Control: public, max-age=31536000, immutable`).
- URLs are content-hashed (`{sha256}.webp`), so updates create new URLs and
  never invalidate.
- Origin = object store; CDN handles 99%+ of reads.

## AR / 3D assets

### Upload flow

Same presigned-URL pattern as images. The worker pipeline is heavier:

1. **Validate** the `.glb` (proper magic bytes, sane mesh count, no embedded
   scripts).
2. **Compress** with Draco (geometry) + Meshopt (vertex attributes).
   Typical reduction: 60–80%.
3. **Generate LOD ladder**:
   - `low.glb` — decimated to ~10k triangles, for mobile devices.
   - `high.glb` — full quality post-compression, for desktops.
4. **Compute pose anchors** if the model is a clothing item (used for
   try-on alignment).
5. **Mark `processed_at`** on `listing_ar_assets`.

### Storage & delivery

- Stored under `ar/{listing_id}/{low|high}.glb` in the object store.
- CDN with `Cache-Control: public, max-age=31536000, immutable`.
- **`Accept-Ranges: bytes`** so the viewer can stream geometry progressively.
- Signed URLs (1-day expiry) prevent hotlinking — important because `.glb`
  files contain user-uploaded geometry that's expensive to bandwidth.

### Client selection

Clients pick a variant based on device hints:

```ts
const variant =
  navigator.deviceMemory && navigator.deviceMemory < 4 ? "low" :
  matchMedia("(max-width: 768px)").matches ? "low" :
  "high";
```

On mobile (Expo), we always use `low.glb` — the mobile GPU can't realistically
render `high` at framerate.

### Why Draco + Meshopt

| Compressor | What it compresses | Decoder cost | Typical ratio |
| --- | --- | --- | --- |
| Draco | Geometry (positions, indices) | Heavy on first load | 4–10× |
| Meshopt | Vertex attributes (normals, UVs) | Light | 2–4× |

Used together, you get most of the size win without crippling startup time.

## What's not implemented yet

The scaffold has the schema (`listing_images`, `listing_ar_assets`) and the
documented design. The actual upload pipeline, worker, and CDN integration
land in `feat/listings-crud` and `feat/ar-pipeline`.
