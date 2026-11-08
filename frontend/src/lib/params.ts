import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

/** Keys that open a record rather than filter the list; changing them keeps the current page. */
const RECORD_KEYS = ["page", "order", "claim"];

/**
 * List filters kept in the URL, so a filtered view can be bookmarked, shared, or reached with Back.
 * Opening a record pushes a history entry (Back closes it); changing a filter replaces the entry.
 */
export function useFilterParams<K extends string>(keys: readonly K[]) {
  const [params, setParams] = useSearchParams();
  const values = Object.fromEntries(keys.map((k) => [k, params.get(k) ?? ""])) as Record<K, string>;

  function update(changes: Partial<Record<string, string | null>>, push = false) {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const [key, value] of Object.entries(changes)) {
          if (value) next.set(key, value);
          else next.delete(key);
        }
        if (!Object.keys(changes).some((k) => RECORD_KEYS.includes(k))) next.delete("page");
        return next;
      },
      { replace: !push },
    );
  }

  return { values, params, update };
}

/** Typing updates the URL after a pause, so every keystroke isn't a request. */
export function useDebouncedSearch(value: string, onSettle: (text: string) => void) {
  const [text, setText] = useState(value);
  const [seen, setSeen] = useState(value);
  // The URL changed from elsewhere (Clear filters, Back): show what it now says.
  if (value !== seen) {
    setSeen(value);
    setText(value);
  }
  useEffect(() => {
    if (text.trim() === value) return;
    const timer = window.setTimeout(() => onSettle(text.trim()), 350);
    return () => window.clearTimeout(timer);
    // onSettle is a new function every render; only the text matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, value]);
  return [text, setText] as const;
}
