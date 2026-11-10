"""Give the installed document libraries Traditional Chinese defaults.

python-docx and python-pptx copy their bundled templates into every new file,
and those leave the East Asian theme font empty (python-docx also pins the
theme font language to ja-JP), so Office falls back to whatever the viewer's
locale picks, often Japanese glyph forms. reportlab starts every canvas and
stylesheet in Helvetica, which has no CJK glyphs at all, and Pillow's default
font has none either. Defaults alone do not cover code that names a Latin-only
or missing font, so reportlab and Pillow also get hooks into
_sandbox_cjk_fonts.py, which falls back to UMingTW for those. reportlab
paragraphs also wrap CJK text by default instead of only at spaces.

With --js PREFIX..., patches the JavaScript packages in those Node/Bun package
trees instead; run it after they are installed. pdf-lib gets the same font
fallbacks (see sandbox-pdf-lib-cjk.cjs), and pptxgenjs gets the zh-TW
language and East Asian theme font the Python templates get.

openpyxl and exceljs get the East Asian theme font, which Excel uses for
theme-font cells. xlsx has no per-run language, so viewers that ignore the
theme (LibreOffice) still pick the CJK font from their own locale.

Each edit must match the installed layout exactly once per expected
occurrence, or the run fails rather than half-applying. Re-running is a no-op.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

EAST_ASIAN_FONT = 'Microsoft JhengHei'
EAST_ASIAN_LANG = 'zh-TW'
PDF_FONT = 'UMingTW'
PDF_FONT_PATH = '/usr/share/fonts/truetype/arphic/uming-tw.ttf'
MARKER = 'sandbox-cjk-defaults-v1'
RUNTIME_MODULE = '_sandbox_cjk_fonts'

EMPTY_EA = '<a:ea typeface=""/>'
FILLED_EA = f'<a:ea typeface="{EAST_ASIAN_FONT}"/>'


def package_dir(name):
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        raise RuntimeError(f'{name} is not installed')
    return Path(spec.origin).parent


def apply(text, edits, where):
    """Apply (old, new, count) edits; an edit already applied is skipped."""
    for old, new, count in edits:
        if text.count(old) == count:
            text = text.replace(old, new)
        elif not (old not in text and new in text):
            raise RuntimeError(f'unsupported layout in {where}: expected {count} x {old!r}')
    return text


def replace_file(path, write):
    """Write through a new inode: Bun and uv hardlink installed files to their
    package cache, and writing in place would patch the cache too."""
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=path.suffix)
    os.close(fd)
    try:
        write(temp)
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    except BaseException:
        os.unlink(temp)
        raise


def write_text(path, text):
    replace_file(path, lambda temp: Path(temp).write_text(text, encoding='utf-8'))
    drop_bytecode(path)


def patch_text(path, edits):
    text = path.read_text(encoding='utf-8')
    patched = apply(text, edits, path)
    if patched != text:
        write_text(path, patched)


def patch_zip(path, parts):
    with zipfile.ZipFile(path) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    missing = set(parts) - {info.filename for info, _ in entries}
    if missing:
        raise RuntimeError(f'unsupported layout in {path}: missing {sorted(missing)}')
    changed = False
    rewritten = []
    for info, data in entries:
        if info.filename in parts:
            text = data.decode('utf-8')
            patched = apply(text, parts[info.filename], f'{path}:{info.filename}')
            if patched != text:
                data = patched.encode('utf-8')
                changed = True
        rewritten.append((info, data))
    if not changed:
        return

    def write(temp):
        with zipfile.ZipFile(temp, 'w') as archive:
            for info, data in rewritten:
                archive.writestr(info, data)

    replace_file(path, write)


def drop_bytecode(source):
    for cached in (source.parent / '__pycache__').glob(source.stem + '.*.pyc'):
        cached.unlink()


def patch_docx():
    templates = package_dir('docx') / 'templates'
    lang = ('w:eastAsia="en-US"', f'w:eastAsia="{EAST_ASIAN_LANG}"', 1)
    patch_zip(templates / 'default.docx', {
        'word/theme/theme1.xml': [(EMPTY_EA, FILLED_EA, 2)],
        'word/styles.xml': [lang],
        'word/stylesWithEffects.xml': [lang],
        # Word resolves minorEastAsia through themeFontLang, so this is what
        # picks the Hant script font when the ea slot is overridden per run.
        'word/settings.xml': [('w:eastAsia="ja-JP"', f'w:eastAsia="{EAST_ASIAN_LANG}"', 1)],
    })
    patch_text(templates / 'default-styles.xml', [lang])


def patch_pptx():
    templates = package_dir('pptx') / 'templates'
    patch_zip(templates / 'default.pptx', {'ppt/theme/theme1.xml': [(EMPTY_EA, FILLED_EA, 2)]})
    # Theme for the notes master python-pptx creates on first notes_slide use.
    patch_text(templates / 'theme.xml', [(EMPTY_EA, FILLED_EA, 2)])


REPORTLAB_SETTINGS = f'''# {MARKER}: written by docker/python-runtime/cjk_defaults.py
# Read by reportlab.rl_config at startup. Fall back to Helvetica when the font
# is absent so a package set paired with an older image keeps working.
import os as _os

__all__ = ('canvas_basefontname', 'defaultGraphicsFontName')

if _os.path.exists({PDF_FONT_PATH!r}):
    canvas_basefontname = defaultGraphicsFontName = {PDF_FONT!r}
else:
    canvas_basefontname = defaultGraphicsFontName = 'Helvetica'
'''

# lib.styles resolves bold/italic names with tt2ps at import time, before any
# font is registered, so the family mapping has to exist up front. UMing has
# no bold or italic face; every variant maps to the regular one.
REPORTLAB_FONTS = f'''
for _b in (0, 1):
    for _i in (0, 1):
        addMapping({PDF_FONT!r}, _b, _i, {PDF_FONT!r})
del _b, _i
'''


def hook(installer):
    return f'''
import {RUNTIME_MODULE}
{RUNTIME_MODULE}.{installer}(globals())
del {RUNTIME_MODULE}
'''


def append_once(path, name, block, anchors, comment='#'):
    """Append a hook block, tagged so a rerun recognises it."""
    tag = f'{comment} {MARKER}: {name}'
    text = path.read_text(encoding='utf-8')
    if tag in text:
        return
    for anchor in anchors:
        if text.count(anchor) != 1:
            raise RuntimeError(f'unsupported layout in {path}: expected 1 x {anchor!r}')
    write_text(path, text.rstrip('\n') + '\n\n' + tag + block)


def install_runtime_module():
    """Copy the module the hooks import next to the patched packages."""
    source = Path(__file__).resolve().parent / f'{RUNTIME_MODULE}.py'
    target = package_dir('reportlab').parent / source.name
    shutil.copyfile(source, target)
    drop_bytecode(target)


def patch_reportlab():
    root = package_dir('reportlab')
    settings = root / 'local_rl_settings.py'
    if settings.exists() and MARKER not in settings.read_text(encoding='utf-8'):
        raise RuntimeError(f'{settings} exists and was not written by this script')
    settings.write_text(REPORTLAB_SETTINGS, encoding='utf-8')
    drop_bytecode(settings)
    rl_config = root / 'rl_config.py'
    if "from reportlab.local_rl_settings import *" not in rl_config.read_text(encoding='utf-8'):
        raise RuntimeError(f'unsupported layout in {rl_config}: local_rl_settings hook missing')
    append_once(root / 'lib' / 'fonts.py', 'mapping', REPORTLAB_FONTS, ['def addMapping(face, bold, italic, psname):'])
    append_once(root / 'pdfbase' / 'pdfmetrics.py', 'hook', hook('install_pdfmetrics'), [
        'def findFontAndRegister(fontName):',
        '        return findFontAndRegister(fontName)',
        'class Font:',
        '    def stringWidth(self, text, size, encoding=\'utf8\'):\n        return instanceStringWidthT1(',
    ])
    append_once(root / 'pdfbase' / 'ttfonts.py', 'hook', hook('install_ttfonts'), [
        'class TTFError(',
        'class TTFont:',
        '    def __init__(self, name, filename, validate=0, subfontIndex=0, asciiReadable=None, shapable=True):',
    ])
    # Base-14 fonts route characters they cannot encode through
    # unicode2T1; send those to UMingTW instead of ZapfDingbats notdef.
    patch_text(root / 'pdfgen' / 'textobject.py', [
        ('                for f, t in pdfmetrics_unicode2T1(text,[font]+font.substitutionFonts):\n',
         f'                import {RUNTIME_MODULE}\n'
         f'                for f, t in {RUNTIME_MODULE}.unicode2T1(text,[font]+font.substitutionFonts,canv._doc):\n',
         1),
        ('R("%s %s Tf %s TL" % (canv._doc.getInternalFontName(f.fontName), fp_str(self._fontsize), fp_str(self._leading)))',
         f'R("%s %s Tf %s TL" % ({RUNTIME_MODULE}.internal_font_name(f, canv._doc), fp_str(self._fontsize), fp_str(self._leading)))',
         1),
    ])
    # Without CJK wrapping a paragraph breaks only at spaces, so mixed text
    # leaves a short line before every run of Chinese too long for the rest.
    patch_text(root / 'lib' / 'styles.py', [
        ("        'wordWrap':None,        #None means do nothing special",
         "        'wordWrap':'CJK',       #None means do nothing special",
         1),
    ])


def patch_openpyxl():
    patch_text(package_dir('openpyxl') / 'writer' / 'theme.py', [(EMPTY_EA, FILLED_EA, 2)])


def patch_pillow():
    path = package_dir('PIL') / 'ImageFont.py'
    append_once(path, 'hook', hook('install_imagefont'), [
        'def truetype(\n',
        'def load_default(size: float | None = None)',
        'from types import ModuleType\n',
        'from ._util import DeferredError, is_path\n',
        'class FreeTypeFont(',
    ])


PDF_LIB_RUNTIME = 'sandbox-pdf-lib-cjk.cjs'
PDF_LIB_HOOK_CJS = '''
require("../sandbox-cjk.cjs")(exports);
'''
PDF_LIB_HOOK_ES = '''
import { PDFDocument as __sandboxDocument, PDFFont as __sandboxFont, PDFPage as __sandboxPage } from "./api/index";
import { CustomFontSubsetEmbedder as __sandboxSubsetEmbedder, StandardFontEmbedder as __sandboxStandardEmbedder } from "./core/index";
import __sandboxCjk from "../sandbox-cjk.cjs";
__sandboxCjk({
    PDFDocument: __sandboxDocument,
    PDFFont: __sandboxFont,
    PDFPage: __sandboxPage,
    CustomFontSubsetEmbedder: __sandboxSubsetEmbedder,
    StandardFontEmbedder: __sandboxStandardEmbedder,
});
'''
# Local tree, npm's global tree, and Bun's global tree.
NODE_MODULE_DIRS = ('node_modules', 'lib/node_modules', 'install/global/node_modules')


def patch_pdf_lib(package):
    shutil.copyfile(Path(__file__).resolve().parent / PDF_LIB_RUNTIME, package / 'sandbox-cjk.cjs')
    append_once(package / 'cjs' / 'index.js', 'hook', PDF_LIB_HOOK_CJS, [
        'tslib_1.__exportStar(require("./api/index"), exports);',
        'tslib_1.__exportStar(require("./core/index"), exports);',
    ], comment='//')
    append_once(package / 'es' / 'index.js', 'hook', PDF_LIB_HOOK_ES, [
        'export * from "./api/index";',
        'export * from "./core/index";',
    ], comment='//')


def patch_pptxgenjs(package):
    # Runs default to lang="en-US", which makes viewers pick a CJK font by
    # their own locale (often Simplified forms) and PowerPoint skip East Asian
    # line-breaking rules. Node loads the cjs build, Bun the es build.
    for build in ('pptxgen.cjs.js', 'pptxgen.es.js'):
        patch_text(package / 'dist' / build, [
            ("(opts.lang || 'en-US')", "(opts.lang || 'zh-TW')", 10),
            ("${opts.lang || 'en-US'}", "${opts.lang || 'zh-TW'}", 4),
            ("(opts.lang ? opts.lang : 'en-US')", "(opts.lang ? opts.lang : 'zh-TW')", 1),
            (' lang="en-US"', f' lang="{EAST_ASIAN_LANG}"', 28),
            # Presentation theme and the workbook embedded with each chart.
            (EMPTY_EA, FILLED_EA, 4),
        ])


def patch_exceljs(package):
    patch_text(package / 'lib' / 'xlsx' / 'xml' / 'theme1.js', [(EMPTY_EA, FILLED_EA, 2)])


JS_PACKAGES = {'pdf-lib': patch_pdf_lib, 'pptxgenjs': patch_pptxgenjs, 'exceljs': patch_exceljs}


def patch_js(prefixes):
    patched = {name: 0 for name in JS_PACKAGES}
    for prefix in prefixes:
        for modules in NODE_MODULE_DIRS:
            for name, patch in JS_PACKAGES.items():
                package = Path(prefix) / modules / name
                if (package / 'package.json').is_file():
                    patch(package)
                    patched[name] += 1
    print('CJK JavaScript defaults applied (' + ', '.join(f'{n}: {c}' for n, c in patched.items()) + ')')


def main(argv):
    if argv[:1] == ['--js']:
        patch_js(argv[1:])
        return
    if argv:
        raise SystemExit('usage: cjk_defaults.py [--js PREFIX...]')
    install_runtime_module()
    patch_docx()
    patch_pptx()
    patch_reportlab()
    patch_openpyxl()
    patch_pillow()
    print(f'CJK document defaults applied ({sys.executable})')


if __name__ == '__main__':
    main(sys.argv[1:])
