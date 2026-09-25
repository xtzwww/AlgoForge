/**
 * Is the web bundle older than the sources it was built from?
 *
 * Usage:
 *   node tools/web-bundle-stale.mjs <desktop-dir>
 *
 * Exit status is always 0 — the answer is printed, because the caller is a
 * `.bat` reading stdout through `for /f`, and a non-zero exit there is
 * indistinguishable from "node is missing". Printing a third state lets the
 * caller treat "cannot tell" as "rebuild" instead of guessing.
 *
 * Prints:
 *   1  stale  — a source file is newer than the bundle, rebuild
 *   0  fresh  — nothing under the build inputs is newer than the output
 *   2  unknown — a directory is missing or unreadable
 *
 * Why this exists
 * ---------------
 * `start-web.bat` builds `dist-web` once and then reuses it. Its original
 * reuse check only asked "does the bundle exist and carry the current
 * port", and its comment said timestamp comparison was skipped on purpose.
 * That is exactly how a whole evening of UI work can be invisible: the
 * services all come up green, the page loads, and it is yesterday's code.
 * The symptom reads as "my change did nothing", which is the most
 * expensive kind of wrong.
 *
 * Why not do this in the `.bat`
 * -----------------------------
 * The comparison needs a recursive walk and a numeric comparison, and cmd
 * has neither. Writing it in cmd means `for /f` quoting, `^|` escapes and
 * 32-bit `if ... GTR` arithmetic — three chances to be subtly wrong, in
 * code that cannot be unit-tested. This file can.
 *
 * What counts as a build input
 * ----------------------------
 * The renderer source, plus the two files at the desktop root that the
 * build reads: `vite.config.web.ts` and `.env.web`. Deliberately NOT the
 * whole `desktop/` tree — `node_modules` alone would make this walk take
 * seconds, and `src/main` is Electron-only, so editing it would trigger
 * pointless web rebuilds.
 */

import { readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

/** Newest mtime under `path`, or `null` if it cannot be read. */
function newestMtime(path) {
  let stats
  try {
    stats = statSync(path)
  } catch {
    return null
  }
  if (!stats.isDirectory()) return stats.mtimeMs

  let newest = stats.mtimeMs
  let entries
  try {
    entries = readdirSync(path, { withFileTypes: true })
  } catch {
    return null
  }
  for (const entry of entries) {
    const child = newestMtime(join(path, entry.name))
    if (child === null) return null
    if (child > newest) newest = child
  }
  return newest
}

const desktopDir = process.argv[2]
if (!desktopDir) {
  console.log(2)
  process.exit(0)
}

const root = resolve(desktopDir)
const bundleDir = join(root, 'dist-web')

// The bundle has to exist before "is it fresh" is a question worth asking.
if (newestMtime(join(bundleDir, 'index.web.html')) === null) {
  console.log(2)
  process.exit(0)
}

const inputs = [
  join(root, 'src', 'renderer'),
  join(root, 'vite.config.web.ts'),
  join(root, '.env.web')
]

let newestSource = 0
for (const input of inputs) {
  const mtime = newestMtime(input)
  if (mtime === null) {
    console.log(2)
    process.exit(0)
  }
  if (mtime > newestSource) newestSource = mtime
}

const newestBundle = newestMtime(bundleDir)
if (newestBundle === null) {
  console.log(2)
  process.exit(0)
}

// A sub-millisecond difference is filesystem noise, not an edit.
console.log(newestSource - newestBundle > 1000 ? 1 : 0)
