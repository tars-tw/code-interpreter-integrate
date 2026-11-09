/**
 * Defaults spread first into every sandbox environment so callers can still
 * override them. The jail mounts only /usr from the rootfs, so the rc file
 * lives there rather than under /etc.
 */
export const SANDBOX_DEFAULT_ENV: Readonly<Record<string, string>> = Object.freeze({
  MATPLOTLIBRC: '/usr/share/matplotlib/matplotlibrc',
});
