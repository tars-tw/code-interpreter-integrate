'use strict';
// Traditional Chinese fallbacks for pdf-lib, copied into the installed package
// as sandbox-cjk.cjs by cjk_defaults.py and called from its cjs and es entry
// points (Node resolves "main", Bun prefers "module").
//
// - fontkit is registered on demand, so embedding a font file just works.
// - A font collection (.ttc) resolves to its Traditional Chinese face and is
//   always subset: unsubset, pdf-lib embeds the whole collection, which no
//   viewer can read.
// - CID-keyed CFF fonts (Noto/Source Han CJK) are swapped for UMingTW: pdf-lib
//   writes a broken font when subsetting them and 14MB per face when not.
// - Text a font cannot render (CJK in StandardFonts.Helvetica, which pdf-lib
//   rejects with "WinAnsi cannot encode", or in a Latin-only custom font,
//   which it draws as notdef boxes) is drawn and measured with UMingTW.
// - drawText's maxWidth only breaks at spaces; CJK text, which has none,
//   overflowed the page instead of wrapping.
const fs = require('fs');

const FONT_PATH = '/usr/share/fonts/truetype/arphic/uming-tw.ttf';
const CJK = /[\u2e80-\u2fdf\u3000-\u30ff\u3100-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]/u;
const TOKENS = new RegExp(`${CJK.source}|\\s+|(?:(?!${CJK.source})\\S)+`, 'gu');
// Closing punctuation stays on the line it ends, even past maxWidth.
const NO_LINE_START = /^[、。，．：；？！）」』》〉】〕｝]$/u;

let fontBytes;
function readFallbackFont() {
  if (fontBytes === undefined) fontBytes = fs.existsSync(FONT_PATH) ? fs.readFileSync(FONT_PATH) : null;
  return fontBytes;
}

const warned = new Set();
function warn(name, why) {
  if (warned.has(name)) return;
  warned.add(name);
  process.stderr.write(`sandbox: font ${JSON.stringify(name)} ${why}; using ${FONT_PATH}\n`);
}

function wrapText(text, maxWidth, widthOf) {
  const lines = [];
  for (const paragraph of text.split(/\r\n|\r|\n/)) {
    let line = '';
    for (const token of paragraph.match(TOKENS) || []) {
      const candidate = line + token;
      if (line && !NO_LINE_START.test(token) && widthOf(candidate.trimEnd()) > maxWidth) {
        lines.push(line.trimEnd());
        line = /^\s+$/u.test(token) ? '' : token;
      } else {
        line = candidate;
      }
    }
    lines.push(line.trimEnd());
  }
  return lines.join('\n');
}

function loadFontkit() {
  const fontkit = require('@pdf-lib/fontkit');
  return typeof fontkit.create === 'function' ? fontkit : fontkit.default;
}

function pickFace(font) {
  const faces = font && Array.isArray(font.fonts) ? font.fonts : null;
  if (!faces || faces.length === 0) return font;
  return faces.find((face) => / TC$/.test(face.familyName || '')) || faces[0];
}

const wrappedFontkits = new WeakMap();

function wrapFontkit(fontkit) {
  if (!fontkit || typeof fontkit.create !== 'function') return fontkit;
  let wrapped = wrappedFontkits.get(fontkit);
  if (!wrapped) {
    wrapped = Object.create(fontkit, {
      create: {
        value: (buffer, postscriptName) => {
          const font = fontkit.create(buffer, postscriptName);
          return postscriptName ? font : pickFace(font);
        },
      },
    });
    wrappedFontkits.set(fontkit, wrapped);
  }
  return wrapped;
}

module.exports = function install(lib) {
  const { PDFDocument, PDFFont, PDFPage, CustomFontSubsetEmbedder, StandardFontEmbedder } = lib;
  if (PDFDocument.prototype.__sandboxCjk) return;
  Object.defineProperty(PDFDocument.prototype, '__sandboxCjk', { value: true });

  let defaultFontkit;
  const assertFontkit = PDFDocument.prototype.assertFontkit;
  PDFDocument.prototype.assertFontkit = function () {
    if (!this.fontkit) this.fontkit = defaultFontkit || (defaultFontkit = loadFontkit());
    return wrapFontkit(assertFontkit.call(this));
  };

  const embedFont = PDFDocument.prototype.embedFont;
  PDFDocument.prototype.embedFont = function (font, options) {
    if (font instanceof Uint8Array || font instanceof ArrayBuffer) {
      const bytes = font instanceof Uint8Array ? font : new Uint8Array(font);
      let face = null;
      try {
        face = this.assertFontkit().create(bytes);
      } catch (error) {
        // Let embedFont report unreadable font data itself.
      }
      const cff = face && face.directory && face.directory.tables['CFF '] ? face['CFF '] : null;
      const fallback = cff && cff.isCIDFont ? readFallbackFont() : null;
      if (fallback) {
        warn(face.postscriptName || 'CID-keyed CFF font', 'cannot be embedded correctly by pdf-lib');
        return embedFont.call(this, fallback, { ...options, subset: true });
      }
      const collection = bytes.length >= 4 && String.fromCharCode(...bytes.subarray(0, 4)) === 'ttcf';
      if (collection) return embedFont.call(this, font, { ...options, subset: true });
    }
    return embedFont.call(this, font, options);
  };

  const fallbacks = new WeakMap();
  const fallbackFor = (doc) => {
    let font = fallbacks.get(doc);
    if (font === undefined) {
      font = null;
      const bytes = readFallbackFont();
      if (bytes) {
        // embedFont is async; drawText needs the font synchronously.
        const embedder = new CustomFontSubsetEmbedder(doc.assertFontkit().create(bytes), bytes);
        font = PDFFont.of(doc.context.nextRef(), doc, embedder);
        doc.fonts.push(font);
      }
      fallbacks.set(doc, font);
    }
    return font;
  };

  const canRender = (font, text) => {
    const embedder = font.embedder;
    let covers;
    if (embedder instanceof StandardFontEmbedder) {
      covers = (cp) => embedder.encoding.canEncodeUnicodeCodePoint(cp);
    } else if (embedder && embedder.font && typeof embedder.font.hasGlyphForCodePoint === 'function') {
      covers = (cp) => embedder.font.hasGlyphForCodePoint(cp);
    } else {
      return true;
    }
    for (const ch of text) {
      const cp = ch.codePointAt(0);
      if (cp >= 0x20 && cp !== 0xa0 && !covers(cp)) return false;
    }
    return true;
  };

  const renderingFont = (font, text) => {
    if (!(font instanceof PDFFont) || typeof text !== 'string' || canRender(font, text)) return font;
    const fallback = fallbackFor(font.doc);
    return fallback && fallback !== font ? fallback : font;
  };

  const drawText = PDFPage.prototype.drawText;
  PDFPage.prototype.drawText = function (text, options) {
    const opts = { ...options };
    const font = opts.font || this.getFont()[0];
    const replacement = renderingFont(font, text);
    if (replacement !== font) opts.font = replacement;
    if (typeof text === 'string' && opts.maxWidth !== undefined && opts.wordBreaks === undefined && CJK.test(text)) {
      const size = opts.size || this.fontSize;
      text = wrapText(text, opts.maxWidth, (t) => replacement.widthOfTextAtSize(t, size));
      delete opts.maxWidth;
    }
    return drawText.call(this, text, opts);
  };

  const widthOfTextAtSize = PDFFont.prototype.widthOfTextAtSize;
  PDFFont.prototype.widthOfTextAtSize = function (text, size) {
    return widthOfTextAtSize.call(renderingFont(this, text), text, size);
  };
};
