// Greys out filter options that would leave no matching rooms, given everything else picked so far.
import type { Commute } from './commute';
import { hasMatch, parseQuery, type SearchIndex } from './filter';
import { formToParams } from './form';

const TITLE = 'No rooms match with this option';

/** Disable each unticked checkbox and unselected <select> option that would bring the count to 0.
 *  Ticked boxes and current select values stay usable, so the user can always back out. */
export function markDeadOptions(form: HTMLFormElement, index: SearchIndex, times?: Map<number, Commute> | null) {
  // Undo the previous pass first, so each candidate is tested against the rest of the current form
  for (const el of form.querySelectorAll<HTMLInputElement | HTMLOptionElement>('[data-dead]')) {
    el.disabled = false;
    el.removeAttribute('data-dead');
    if (el instanceof HTMLInputElement) el.closest('label')?.removeAttribute('title');
  }
  const matches = () => hasMatch(index, parseQuery(formToParams(form)), times);
  const dead: (HTMLInputElement | HTMLOptionElement)[] = [];

  for (const box of form.querySelectorAll<HTMLInputElement>('input[type="checkbox"][name]')) {
    if (box.checked || box.disabled) continue;
    box.checked = true;
    if (!matches()) dead.push(box);
    box.checked = false;
  }
  for (const sel of form.querySelectorAll<HTMLSelectElement>('select[name]')) {
    const current = sel.value;
    for (const opt of sel.options) {
      if (opt.value === '' || opt.value === current || opt.disabled) continue;
      sel.value = opt.value;
      if (!matches()) dead.push(opt);
    }
    sel.value = current;
  }

  for (const el of dead) {
    el.disabled = true;
    el.setAttribute('data-dead', '');
    if (el instanceof HTMLInputElement) el.closest('label')?.setAttribute('title', TITLE);
  }
}
