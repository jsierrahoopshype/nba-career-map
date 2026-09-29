# Vendored front-end libraries

Served from this site instead of unpkg.com, so a visitor's browser makes no
request to a third-party CDN for them. Each file is byte-for-byte the one in
the npm package at the pinned version (the same file unpkg served), with the
package's own licence file beside it.

| Directory | Package | Files | Licence |
|---|---|---|---|
| `leaflet-1.9.4/` | `leaflet@1.9.4` | `dist/leaflet.js`, `dist/leaflet.css`, `dist/images/*` | BSD-2-Clause (`LICENSE`) |
| `maplibre-gl-5.24.0/` | `maplibre-gl@5.24.0` | `dist/maplibre-gl.js`, `dist/maplibre-gl.css` | BSD-3-Clause (`LICENSE.txt`) |
| `maplibre-gl-leaflet-0.1.4/` | `@maplibre/maplibre-gl-leaflet@0.1.4` | `leaflet-maplibre-gl.js` | ISC (`LICENSE`) |

Source maps are not vendored; the `sourceMappingURL` comments only matter to
browser dev tools. To upgrade, `npm pack <pkg>@<version>`, copy the same files
into a new versioned directory and update the paths in `index.html`.
