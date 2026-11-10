/**
 * Defaults spread first into every sandbox environment so callers can still
 * override them. The jail mounts only /usr from the rootfs, so the config
 * files live there rather than under /etc.
 */
export const SANDBOX_DEFAULT_ENV: Readonly<Record<string, string>> = Object.freeze({
  MATPLOTLIBRC: '/usr/share/matplotlib/matplotlibrc',
  FONTCONFIG_FILE: '/usr/share/fontconfig/sandbox/fonts.conf',
  // wordcloud's default font when font_path is not passed; its bundled one has no CJK glyphs.
  FONT_PATH: '/usr/share/fonts/truetype/arphic/uming-tw.ttf',
});
