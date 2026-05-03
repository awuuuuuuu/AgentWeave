/**
 * Module-level state for passing a File object to the upload settings page.
 * Lives in JS memory — cleared on page refresh (user is redirected back to detail page).
 */

let _file: File | null = null;

export const uploadState = {
  set(file: File) { _file = file; },
  get(): File | null { return _file; },
  clear() { _file = null; },
};
