/**
 * A page's override for its own breadcrumb segment (PU-7).
 *
 * The topbar builds crumbs from the route table, but one route cannot be
 * labelled from static metadata: `/problems/:id` carries an id, not a
 * name. The crumb for it has to be `题库 / 二分查找`, and only the page
 * knows that `12` is called 二分查找 — it is the thing that fetched it.
 *
 * So a page publishes a label and the topbar prefers it over the route's
 * static `meta.title`. The rule in the topbar is therefore uniform
 * ("published label, else `meta.title`") rather than a special case for
 * one route name, and a future detail page gets the same behaviour by
 * publishing a string.
 *
 * This is the second piece of shared state in the frontend; see
 * `useSidecarStatus.ts` for why it is a singleton composable rather than
 * a Pinia store this phase.
 *
 * The dependency direction matters: a page writes, the shell reads. The
 * shell never asks a page for anything, so a page that publishes nothing
 * degrades to its static title instead of breaking the topbar.
 */

import { readonly, ref, type Ref } from 'vue'

const crumb = ref<string | null>(null)

/**
 * Publish this page's crumb label, or `null` to fall back to the route's
 * `meta.title`.
 *
 * A page should clear this when it unmounts. Leaving a stale value behind
 * is not merely cosmetic: the next page would inherit a label naming
 * something it is not showing.
 */
export function publishPageCrumb(value: string | null): void {
  crumb.value = value
}

/** Read the published label. `null` means "use the route's own title". */
export function usePageCrumb(): Readonly<Ref<string | null>> {
  return readonly(crumb)
}
