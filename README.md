# redline-data-cleaner
AI-assisted data cleaning tool — detects nulls, whitespace, duplicates"

## Photo Collector (`photo-collector/`)

A phone-friendly web app to pick photos, see the date each was taken, and build
collections by **person** and **date**.

- **Add photos** from your phone — the date taken is read from the photo's camera
  (EXIF) data; if missing, the file date is used. You can edit any date.
- **Tag people**: tap a photo to add names, or use **Select** to tag many at once.
- **Filter** by one or more people (any / all) and a from–to date range.
- **Save as collection** — collections stay up to date as you tag more photos.
  Open a collection to **Share** (phone share sheet) or **Download** its photos.
- Everything is stored on your device (IndexedDB); nothing is uploaded.

Run it: serve the folder (e.g. `cd photo-collector && python3 -m http.server`)
and open it in your phone's browser, or host it on GitHub Pages.
