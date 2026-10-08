/**
 * A list row that opens from a click anywhere on it, without nesting controls
 * inside a control: the row's name is the link (or button), stretched over
 * the row with a pseudo-element, and the row's own actions sit above it as
 * siblings. Keyboard and screen-reader users reach the name, then each action.
 */
export const LIST_ROW_CLASS = "relative flex flex-col justify-between gap-3 rounded-lg border p-4 transition-colors hover:bg-muted/50 sm:flex-row sm:items-center";

/** The name link or button: its hit area and focus ring cover the whole row. */
export const LIST_ROW_TARGET_CLASS = "cursor-pointer text-left after:absolute after:inset-0 after:rounded-lg focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring";

/** The row's actions, raised above the stretched name. */
export const LIST_ROW_ACTIONS_CLASS = "relative z-10 flex w-full flex-wrap items-center justify-end gap-1 sm:w-auto";
