import { useEffect, type RefObject } from "react";

/**
 * Selector for elements considered "focusable" inside a modal. Aligned with
 * the WAI-ARIA APG dialog pattern's tabbable-element semantics: includes
 * native interactive elements that aren't disabled, plus any element with a
 * non-negative `tabindex`. Elements with `tabindex="-1"` are excluded — they
 * may be programmatically focusable but Tab/Shift+Tab should skip them.
 *
 * `contenteditable` and audio/video are intentionally not bothered with;
 * the modal in this codebase only ever holds buttons, anchors, and inputs.
 */
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter((el) => !el.hasAttribute("hidden"));
}

/**
 * Trap focus inside `containerRef` while `active` is true.
 *
 * Behaviour:
 *   - Tab from the last focusable element wraps to the first.
 *   - Shift+Tab from the first wraps to the last.
 *   - Other Tab presses fall through to the browser's default handling so
 *     focus order between modal elements is preserved.
 *
 * The hook does NOT manage initial focus or focus restoration — those are
 * separate concerns the consumer drives explicitly (initial focus on the
 * primary action; restoration to the trigger element when the consumer
 * decides the modal closes). Bundling them into one hook would couple the
 * trap's lifecycle to the consumer's component-mount lifecycle, which
 * makes Esc-restore-on-close fragile when the modal unmounts on its own
 * state transitions.
 */
export function useFocusTrap(
  containerRef: RefObject<HTMLElement>,
  active: boolean,
): void {
  useEffect(() => {
    if (!active) return;
    const container = containerRef.current;
    if (!container) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      if (!container) return;
      const focusables = getFocusableElements(container);
      if (focusables.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;

      if (event.shiftKey) {
        if (active === first || !container.contains(active)) {
          event.preventDefault();
          last.focus();
        }
      } else {
        if (active === last || !container.contains(active)) {
          event.preventDefault();
          first.focus();
        }
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [containerRef, active]);
}
