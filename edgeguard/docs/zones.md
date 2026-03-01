# Zone Configuration

Zones are loaded from `config/zones.sample.json` (or `ZONES_PATH`).

## Schema
```json
{
  "camera_id": "cam01",
  "zones": {
    "shelf_zone": [[x1, y1], [x2, y2], ...],
    "exit_zone": [[...]],
    "checkout_zone": [[...]],
    "bagging_zone": [[...]],
    "scanner_zone": [[...]]
  }
}
```

## Zone Intent
- `shelf_zone`: area where item interaction starts
- `exit_zone`: doorway / egress area
- `checkout_zone`: queue or checkout context
- `bagging_zone`: where bag placement is expected
- `scanner_zone`: scanner interaction proxy (self-checkout mode)

## Coordinate Notes
- Points are pixel coordinates in the source frame.
- Use polygon points in clockwise or counter-clockwise order.
- Keep polygons tight around the actual region.
- If zones are missing, EdgeGuard auto-generates defaults based on frame size.

## Practical Setup
1. Capture a sample frame from your camera/video.
2. Mark polygons manually in any annotation tool.
3. Save points into `config/zones.sample.json`.
4. Restart API.
5. Validate overlays in Streamlit live view.

## Validation
The helper `is_point_in_zone(point, polygon)` uses `cv2.pointPolygonTest`.
Boundary points are treated as inside.
