"""Traditional Chinese fallbacks for code that never asks for a CJK font.

Installed into site-packages by cjk_defaults.py and called from one-line hooks
it appends to reportlab and Pillow. Every fallback degrades to the library's
own behaviour when the font file is absent (a package set paired with an older
image).
"""
from collections import namedtuple
import os
import re
import sys

FONT_NAME = 'UMingTW'
FONT_PATH = '/usr/share/fonts/truetype/arphic/uming-tw.ttf'

_warned = set()


def _warn(requested, why):
    if requested not in _warned:
        _warned.add(requested)
        print(f'sandbox: font {requested!r} {why}; using {FONT_PATH}', file=sys.stderr)


# ---------------------------------------------------------------------------
# reportlab
# ---------------------------------------------------------------------------

# A run of the fallback font: textobject needs the subset to name the PDF font.
CJKRun = namedtuple('CJKRun', 'font subset')


def _fallback_ttfont():
    if not os.path.exists(FONT_PATH):
        return None
    from reportlab.pdfbase import pdfmetrics
    return pdfmetrics.getFont(FONT_NAME)


def _encoding(font):
    return 'UTF16' if 'UCS-2' in font.encName else font.encName


def _segments(text, fonts):
    """Split text into (encodable_by_type1_chain, chunk) runs."""
    encodings = [_encoding(f) for f in fonts]
    runs = []
    for ch in text:
        ok = False
        for enc in encodings:
            try:
                ch.encode(enc)
            except UnicodeEncodeError:
                continue
            ok = True
            break
        if runs and runs[-1][0] == ok:
            runs[-1][1].append(ch)
        else:
            runs.append((ok, [ch]))
    return [(ok, ''.join(chars)) for ok, chars in runs]


def unicode2T1(text, fonts, doc):
    """pdfmetrics.unicode2T1, sending what no base-14 font can encode to UMingTW.

    Without this, CJK text in Helvetica, Times or Courier silently comes out
    as ZapfDingbats notdef boxes.
    """
    from reportlab.pdfbase.pdfmetrics import unicode2T1 as original
    try:
        text.encode(_encoding(fonts[0]))
        return original(text, fonts)
    except UnicodeEncodeError:
        pass
    fallback = _fallback_ttfont()
    if fallback is None:
        return original(text, fonts)
    pairs = []
    for ok, chunk in _segments(text, fonts):
        if ok:
            pairs.extend(original(chunk, fonts))
        else:
            pairs.extend((CJKRun(fallback, subset), t) for subset, t in fallback.splitString(chunk, doc))
    return pairs


def internal_font_name(font, doc):
    if isinstance(font, CJKRun):
        return font.font.getSubsetInternalName(font.subset, doc)
    return doc.getInternalFontName(font.fontName)


def install_pdfmetrics(ns):
    """Hook reportlab.pdfbase.pdfmetrics; ns is that module's globals()."""
    find_font_and_register = ns['findFontAndRegister']

    def findFontAndRegister(fontName):
        if fontName == FONT_NAME and os.path.exists(FONT_PATH):
            from reportlab.pdfbase.ttfonts import TTFont
            font = TTFont(FONT_NAME, FONT_PATH)
            ns['registerFont'](font)
            return font
        return find_font_and_register(fontName)

    ns['findFontAndRegister'] = findFontAndRegister

    font_class = ns['Font']
    string_width = font_class.stringWidth

    def stringWidth(self, text, size, encoding='utf8'):
        if isinstance(text, bytes):
            text = text.decode(encoding)
        fonts = [self] + self.substitutionFonts
        try:
            text.encode(_encoding(self))
        except UnicodeEncodeError:
            fallback = _fallback_ttfont()
            if fallback is not None:
                return sum(
                    string_width(self, chunk, size) if ok else fallback.stringWidth(chunk, size)
                    for ok, chunk in _segments(text, fonts)
                )
        return string_width(self, text, size, encoding)

    font_class.stringWidth = stringWidth


# File names of CJK fonts code reaches for: Windows and macOS system fonts,
# and the CFF-outline Noto/Source Han fonts reportlab cannot load.
_CJK_FONT_FILE = re.compile(
    r'cjk|msjh|msyh|mingliu|simsun|simhei|simkai|simfang|kaiu|kaiti|jhenghei|yahei'
    r'|pingfang|heiti|songti|hiragino|sourcehan|wqy|wenquanyi|droidsansfallback|uming|ukai'
    r'|(?:sans|serif)(?:tc|sc|hk|jp|kr)\b|[-_ ](?:tc|sc|hk|tw)\b',
    re.IGNORECASE,
)


def install_ttfonts(ns):
    """Hook reportlab.pdfbase.ttfonts; ns is that module's globals().

    Registering a CJK font that is missing here (msjh.ttc, PingFang) or that
    reportlab cannot read (Noto CJK is CFF) loads UMingTW under the requested
    name instead. Other failures still raise: svglib probes guessed file
    names and relies on the error to move on.
    """
    ttfont_class = ns['TTFont']
    errors = (ns['TTFError'], OSError)
    init = ttfont_class.__init__

    def __init__(self, name, filename, *args, **kwargs):
        try:
            init(self, name, filename, *args, **kwargs)
        except errors as error:
            if not isinstance(filename, (str, os.PathLike)) or not os.path.exists(FONT_PATH):
                raise
            stem = os.path.splitext(os.path.basename(os.fspath(filename)))[0]
            if not _CJK_FONT_FILE.search(stem):
                raise
            if os.path.realpath(filename) == os.path.realpath(FONT_PATH):
                raise
            _warn(os.fspath(filename), f'cannot be used by reportlab ({error})')
            kwargs.pop('subfontIndex', None)
            if len(args) >= 2:
                args = args[:1] + (0,) + args[2:]
            init(self, name, FONT_PATH, *args, **kwargs)

    ttfont_class.__init__ = __init__


# ---------------------------------------------------------------------------
# Pillow
# ---------------------------------------------------------------------------

_NOTO_CJK = re.compile(r'^Noto(Sans|Serif)(Mono)?CJK.*\.ttc$', re.IGNORECASE)


def _noto_tc_index(font_class, path, size, encoding, layout_engine):
    """Index of the Traditional Chinese face in a Noto CJK collection."""
    for index in range(16):
        try:
            face = font_class(path, size, index, encoding, layout_engine)
        except OSError:
            return None
        family = face.getname()[0] or ''
        if family.endswith(' TC'):
            return index
    return None


def install_imagefont(ns):
    """Hook PIL.ImageFont; ns is that module's globals()."""
    truetype = ns['truetype']
    load_default = ns['load_default']
    font_class = ns['FreeTypeFont']
    is_path = ns['is_path']

    def patched_truetype(font=None, size=10, index=0, encoding='', layout_engine=None):
        try:
            loaded = truetype(font, size, index, encoding, layout_engine)
        except OSError:
            if not is_path(font) or not os.path.exists(FONT_PATH):
                raise
            if os.path.exists(font):
                raise
            _warn(os.fsdecode(font), 'was not found')
            return truetype(FONT_PATH, size, 0, encoding, layout_engine)
        path = getattr(loaded, 'path', None)
        # Collections open on their first face, the Japanese one for Noto.
        if index == 0 and isinstance(path, str) and _NOTO_CJK.match(os.path.basename(path)):
            tc = _noto_tc_index(font_class, path, size, encoding, layout_engine)
            if tc:
                return truetype(path, size, tc, encoding, layout_engine)
        return loaded

    def patched_load_default(size=None):
        core = ns['core']
        if os.path.exists(FONT_PATH) and isinstance(core, ns['ModuleType']):
            return truetype(FONT_PATH, 10 if size is None else size)
        return load_default(size)

    patched_truetype.__doc__ = truetype.__doc__
    patched_load_default.__doc__ = load_default.__doc__
    ns['truetype'] = patched_truetype
    ns['load_default'] = patched_load_default
