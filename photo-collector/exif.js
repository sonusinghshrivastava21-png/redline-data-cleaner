// Minimal EXIF reader: finds the date a JPEG photo was taken.
// Works in browsers (window.readExifDate) and Node (module.exports) for testing.
(function (root) {
  const TAG_EXIF_IFD = 0x8769;
  const TAG_DATETIME = 0x0132;
  const TAG_DATETIME_ORIGINAL = 0x9003;
  const TAG_DATETIME_DIGITIZED = 0x9004;

  // "2024:05:17 14:03:22" -> Date (local time), or null
  function parseExifDateString(s) {
    const m = /^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/.exec(s || '');
    if (!m) return null;
    const [y, mo, d, h, mi, se] = m.slice(1).map(Number);
    if (y < 1900 || mo < 1 || mo > 12 || d < 1 || d > 31) return null;
    return new Date(y, mo - 1, d, h, mi, se);
  }

  function readIfd(view, tiffStart, offset, little) {
    const tags = {};
    if (tiffStart + offset + 2 > view.byteLength) return tags;
    const count = view.getUint16(tiffStart + offset, little);
    for (let i = 0; i < count; i++) {
      const entry = tiffStart + offset + 2 + i * 12;
      if (entry + 12 > view.byteLength) break;
      const tag = view.getUint16(entry, little);
      const type = view.getUint16(entry + 2, little);
      const num = view.getUint32(entry + 4, little);
      if (type === 2) {
        // ASCII string: inline if <= 4 bytes, otherwise at offset
        const start = num <= 4 ? entry + 8 : tiffStart + view.getUint32(entry + 8, little);
        let str = '';
        for (let j = 0; j < num && start + j < view.byteLength; j++) {
          const c = view.getUint8(start + j);
          if (c === 0) break;
          str += String.fromCharCode(c);
        }
        tags[tag] = str;
      } else if (type === 4 || type === 13) {
        tags[tag] = view.getUint32(entry + 8, little);
      } else if (type === 3) {
        tags[tag] = view.getUint16(entry + 8, little);
      }
    }
    return tags;
  }

  // buffer: ArrayBuffer with (at least the start of) a JPEG file
  function readExifDateFromBuffer(buffer) {
    const view = new DataView(buffer);
    if (view.byteLength < 4 || view.getUint16(0) !== 0xffd8) return null;
    let pos = 2;
    while (pos + 4 <= view.byteLength) {
      const marker = view.getUint16(pos);
      if ((marker & 0xff00) !== 0xff00) return null;
      const size = view.getUint16(pos + 2);
      if (marker === 0xffe1 && pos + 10 <= view.byteLength &&
          view.getUint32(pos + 4) === 0x45786966) { // "Exif"
        const tiff = pos + 10;
        const little = view.getUint16(tiff) === 0x4949;
        const ifd0 = readIfd(view, tiff, view.getUint32(tiff + 4, little), little);
        let exif = {};
        if (ifd0[TAG_EXIF_IFD]) exif = readIfd(view, tiff, ifd0[TAG_EXIF_IFD], little);
        return parseExifDateString(exif[TAG_DATETIME_ORIGINAL]) ||
               parseExifDateString(exif[TAG_DATETIME_DIGITIZED]) ||
               parseExifDateString(ifd0[TAG_DATETIME]);
      }
      if (marker === 0xffda) return null; // start of image data, no EXIF found
      pos += 2 + size;
    }
    return null;
  }

  // Browser helper: File/Blob -> Promise<Date|null>
  async function readExifDate(file) {
    try {
      const buf = await file.slice(0, 256 * 1024).arrayBuffer();
      return readExifDateFromBuffer(buf);
    } catch (e) {
      return null;
    }
  }

  const api = { readExifDate, readExifDateFromBuffer, parseExifDateString };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else Object.assign(root, api);
})(typeof window !== 'undefined' ? window : globalThis);
