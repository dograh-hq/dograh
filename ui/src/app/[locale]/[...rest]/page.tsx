// Catch-all route for locale-prefixed paths that haven't been migrated yet.
// Pages will be incrementally migrated in Tasks 2-4.
// When a page hasn't been migrated yet, this renders the original page
// by importing it from the non-locale path.

import { notFound } from "next/navigation";

export default function CatchAllPage() {
  notFound();
}
