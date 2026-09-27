/**
 * Is the running sidecar older than the sources it was started from?
 *
 * Usage:
 *   node tools/sidecar-stale.mjs <sidecar-dir> <port>
 *
 * Prints:
 *   1  stale   — the process loaded different sources than the ones on disk
 *   0  fresh   — the process reports the fingerprint of the current sources
 *   2  unknown — nothing answered, or either side cannot compute a stamp
 *
 * Exit status is always 0 — the answer is printed, because the caller is a
 * `.bat` reading stdout through `for /f`, and a non-zero exit there is
 * indistinguishable from "node is missing". The caller treats anything
 * other than the literal `0` as "restart", which is the safe direction: a
 * needless restart costs a few seconds and no data (sessions live in
 * PostgreSQL), while a needless reuse costs an afternoon.
 *
 * Why this exists
 * ---------------
 * `start-web.bat` probes `/health` and reuses whatever answers. That is a
 * *liveness* check, not a *currency* check: the process may predate the
 * code on disk by hours. The symptom is a live backend answering `404` for
 * a route that plainly exists in the source — registration, in the case
 * that prompted this file — which reads as a routing or frontend bug and
 * is neither. `/health` publishes the fingerprint the process loaded
 * (`sidecar/app/code_stamp.py`); this recomputes it from disk and compares.
 *
 * The algorithm must match `sidecar/app/code_stamp.py` exactly
 * ------------------------------------------------------------
 * sha256 over every file under `app/`, in sorted POSIX-relative-path order,
 * feeding `<relpath>\0<bytes>\0` per file, hex, first 16 characters.
 * Skipped: directories named `__pycache__`, names ending in `.pyc`/`.pyo`,
 * and symlinks. Change one side and the other has to change too, or every
 * launch restarts a healthy backend.
 */

import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const EXCLUDED_DIR = '__pycache__'
const EXCLUDED_SUFFIXES = ['.pyc', '.pyo']
const STAMP_LENGTH = 16

/**
 * Files under `dir` as `{ rel, abs }`, `rel` using forward slashes.
 * Returns `null` when the tree cannot be read.
 */
function collect(dir, prefix = '', out = []) {
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return null
  }

  for (const entry of entries) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name

    if (entry.isDirectory()) {
      if (entry.name === EXCLUDED_DIR) continue
      if (collect(join(dir, entry.name), rel, out) === null) return null
      continue
    }

    // Dirent, not statSync: symlinks are deliberately not followed, matching
    // the Python side. Nothing in `app/` is a symlink today; this just keeps
    // the two implementations from disagreeing if something ever is.
    if (!entry.isFile()) continue
    if (EXCLUDED_SUFFIXES.some((suffix) => entry.name.endsWith(suffix))) continue

    out.push({ rel, abs: join(dir, entry.name) })
  }
  return out
}

/** The fingerprint of `<sidecarDir>/app` on disk, or `null`. */
function localStamp(appDir) {
  const files = collect(appDir)
  if (files === null || files.length === 0) return null

  // Plain code-unit ordering: every path here is ASCII, where it matches
  // Python's code-point ordering.
  files.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0))

  const hash = createHash('sha256')
  const separator = Buffer.from([0])
  for (const file of files) {
    let bytes
    try {
      bytes = readFileSync(file.abs)
    } catch {
      return null
    }
    hash.update(Buffer.from(file.rel, 'utf8'))
    hash.update(separator)
    hash.update(bytes)
    hash.update(separator)
  }
  return hash.digest('hex').slice(0, STAMP_LENGTH)
}

/** The `code_stamp` a listening sidecar reports, or `null`. */
async function runningStamp(port) {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/health`, {
      signal: AbortSignal.timeout(3000)
    })
    if (!response.ok) return null
    const body = await response.json()
    return typeof body.code_stamp === 'string' && body.code_stamp ? body.code_stamp : null
  } catch {
    return null
  }
}

const sidecarDir = process.argv[2]
const port = process.argv[3]
if (!sidecarDir || !port) {
  console.log(2)
  process.exit(0)
}

// A backend that predates this check reports no stamp at all. That is the
// stale case, and it lands in `2` on purpose: the caller restarts on
// anything but `0`, which is the right action for both.
const running = await runningStamp(port)
if (running === null) {
  console.log(2)
  process.exit(0)
}

const local = localStamp(join(resolve(sidecarDir), 'app'))
if (local === null) {
  console.log(2)
  process.exit(0)
}

console.log(local === running ? 0 : 1)
