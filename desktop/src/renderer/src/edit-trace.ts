/**
 * edit-trace — records how the code in the editor came to be (C1).
 *
 * The project's title promises learning-**process** analysis, and this is
 * the only place in the client that observes the process. Everything else
 * the backend receives — the code, the verdict, the timings — describes
 * the *result* of a submission; this describes the work that produced it.
 *
 * Why it is a module-level singleton
 * ----------------------------------
 * The events are produced by `CodeEditor.vue` (which owns the Monaco
 * instance and is therefore the only thing that can see a paste) and
 * consumed by `Practice.vue` (which owns the WebSocket and therefore the
 * only thing that submits). Passing that through props and emits would
 * mean threading a mutable buffer through two components that otherwise
 * know nothing about each other, for data that is, by definition,
 * ambient to the whole session. A session-scoped recorder is the honest
 * shape of the thing.
 *
 * What is recorded, and what is not
 * ---------------------------------
 * Counts, sizes, and millisecond offsets — never any code. The recorder
 * never reads the buffer's text; `CodeEditor` hands it the *number of
 * characters* each change involved. That keeps this module from becoming
 * a place where source code is copied around, and it means the payload
 * cannot leak anything a teacher should not see.
 *
 * Only the derived summary survives anyway: the backend turns this trace
 * into about ten numbers and discards the events (see
 * `sidecar/app/process_trace.py`). Keeping the raw list bounded here is
 * still worth doing, because it is uploaded with every submission.
 */

/** What the student did. Mirrors `EVENT_KINDS` on the backend. */
export type EditEventKind = 'open' | 'type' | 'delete' | 'paste' | 'run'

export interface EditEvent {
  /** Milliseconds since the session started — never a wall-clock time. */
  t: number
  kind: EditEventKind
  /** Characters involved: pasted size, typed size, or deleted size. */
  chars: number
  /** Document length *after* the event. */
  length: number
}

export interface ProcessTracePayload {
  events: EditEvent[]
}

/** The measurements the sidecar derives from one trace. */
export interface ProcessFeatures {
  /** Milliseconds from the session's first event to its last. */
  duration_ms: number
  edit_count: number
  paste_count: number
  pasted_chars: number
  final_length: number
  /** `pasted_chars / final_length`. */
  paste_ratio: number
  post_paste_edits: number
  post_paste_chars: number
  /** `post_paste_chars / pasted_chars`, or null when nothing was pasted. */
  rewrite_ratio: number | null
  runs_before_submit: number
}

/**
 * What the sidecar concluded, and the evidence behind it.
 *
 * `flagged` is a *categorical* observation, never a score — there is no
 * "AI probability" anywhere in this system, by design. See
 * `sidecar/app/process_trace.py` for why: falsely flagging a student who
 * wrote their own code costs their trust, and no apology undoes it.
 */
export interface ProcessView {
  features: ProcessFeatures
  flagged: boolean
  /** Chinese sentences, most specific first. Always present. */
  reasons: string[]
  summary: string
}

/**
 * Upper bound on a session's events.
 *
 * The backend caps at 2000 and rejects a longer list outright; staying
 * below it here means a long session is summarised by its first thousand
 * events rather than dropped entirely. The features are counts and
 * ratios — they saturate long before this.
 */
const MAX_EVENTS = 1000

let startedAt = Date.now()
let docLength = 0
let events: EditEvent[] = []

function offset(): number {
  return Date.now() - startedAt
}

function push(kind: EditEventKind, chars: number): void {
  if (events.length >= MAX_EVENTS) return
  events.push({ t: offset(), kind, chars, length: docLength })
}

/**
 * Convert the most recent change into a paste.
 *
 * Monaco applies the edit first and fires `onDidPaste` afterwards, from
 * the same synchronous call — so by the time this runs, the change has
 * already been recorded as a `type`. Rewriting it in place is how the
 * two are reconciled without either guessing ("an insertion of more than
 * 40 characters was probably a paste", which is wrong for a held key and
 * wrong again for a one-line paste) or counting the paste twice.
 *
 * A paste that replaces a selection is recorded at its inserted size,
 * not the net change: what matters is how much text arrived from
 * outside, and a paste over a selection still brought all of it.
 */
let lastInserted = 0

function markLastAsPaste(): void {
  const last = events[events.length - 1]
  if (!last) return
  last.kind = 'paste'
  last.chars = lastInserted
}

export const editTrace = {
  /**
   * Start a new editing session.
   *
   * Called when the buffer is replaced wholesale — loading a problem,
   * switching language, mounting the editor. Those are the only points
   * at which "the code being worked on" changes identity, so they are
   * the only points at which a trace accumulated so far stops
   * describing the current submission.
   */
  reset(length = 0): void {
    startedAt = Date.now()
    docLength = length
    events = []
    push('open', 0)
  },

  /**
   * Record one change to the buffer.
   *
   * Args:
   *   inserted: Characters added.
   *   removed: Characters removed (the replaced selection, for a paste).
   *   isUndoRedo: An undo or a redo. Applied to the running length but
   *     not recorded as an event: undoing is not work, and counting it
   *     would let a student inflate their rewrite ratio by pressing
   *     Ctrl+Z a few times.
   */
  applyChange(inserted: number, removed: number, isUndoRedo = false): void {
    docLength += inserted - removed
    if (docLength < 0) docLength = 0
    lastInserted = inserted

    if (inserted === 0 && removed === 0) return
    if (isUndoRedo) return

    if (inserted >= removed) {
      push('type', inserted)
    } else {
      push('delete', removed)
    }
  },

  /** See `markLastAsPaste`. */
  markPaste(): void {
    markLastAsPaste()
  },

  /**
   * Record that the student asked for a verdict.
   *
   * Called for 运行 only, not for 提交: the backend reports this as
   * "runs before this submission", and counting the submission itself
   * would make the number one higher than its name. Repeated submissions
   * are already visible as separate rows.
   */
  recordRun(): void {
    push('run', 0)
  },

  /**
   * The trace to send with the current submission.
   *
   * A copy, because the session keeps running afterwards — the next
   * submission's trace includes everything up to *that* point, which is
   * what makes a second attempt look different from a first one.
   */
  snapshot(): ProcessTracePayload {
    return { events: events.slice() }
  },

  /** Document length as tracked here. Exposed for tests. */
  get length(): number {
    return docLength
  },

  /** Event count as tracked here. Exposed for tests. */
  get size(): number {
    return events.length
  }
}
