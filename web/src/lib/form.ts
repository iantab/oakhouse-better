// Keeps a filter <form> in sync with the URL query string.
import { DEFAULT_AVAIL, parseQuery } from './filter';

const CARRIED = ['area', 'st', 'line'];

/** Prefill inputs from `params` and carry the area/station/line picks along as hidden inputs. */
export function syncFormFromUrl(form: HTMLFormElement, params: URLSearchParams) {
  const avail: string[] = parseQuery(params).avail; // applies the default + old status=all links
  for (const el of form.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input[name], select[name]')) {
    const values = params.getAll(el.name).flatMap((v) => v.split(','));
    if (el instanceof HTMLSelectElement) {
      el.value = values[0] ?? '';
    } else if (el.name === 'avail') {
      el.checked = avail.includes(el.value);
    } else if (el.type === 'checkbox') {
      el.checked = values.includes(el.value);
    } else if (el.type === 'radio') {
      if (values.length) el.checked = values.includes(el.value);
    }
  }
  // Open collapsed filter groups that contain a ticked box
  for (const d of form.querySelectorAll('details')) {
    if (d.querySelector('input:checked')) d.open = true;
  }
  const carry = form.querySelector<HTMLElement>('[data-carry]');
  if (!carry) return;
  carry.replaceChildren();
  for (const name of CARRIED) {
    for (const value of params.getAll(name)) {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = name;
      input.value = value;
      carry.append(input);
    }
  }
}

/** The form's current values as query params, dropping empty values and defaults. */
export function formToParams(form: HTMLFormElement): URLSearchParams {
  const params = new URLSearchParams();
  for (const [name, value] of new FormData(form)) {
    if (typeof value !== 'string' || value === '') continue;
    if (name === 'sort' && value === 'price') continue;
    params.append(name, value);
  }
  const avail = params.getAll('avail');
  if (avail.length === DEFAULT_AVAIL.length && DEFAULT_AVAIL.every((v) => avail.includes(v))) params.delete('avail');
  return params;
}

/** Stop the last ticked box of a checkbox group (e.g. availability) from being unticked. */
export function keepOneChecked(form: HTMLFormElement, name: string) {
  // Capture phase, so the page's own change handlers already see the corrected state.
  form.addEventListener(
    'change',
    (e) => {
      const box = e.target as HTMLInputElement;
      if (box.name !== name || box.checked) return;
      if (!form.querySelector(`input[name="${name}"]:checked`)) box.checked = true;
    },
    true,
  );
}

/** Human summary of the carried location picks, e.g. "2 areas" / "Yamanote Line + 3 stations". */
export function describeLocation(params: URLSearchParams, labels: { lines?: Map<string, string> } = {}) {
  const parts: string[] = [];
  const areas = params.getAll('area');
  if (areas.length) {
    parts.push(
      areas.length <= 3
        ? areas.map((a) => a.split('/').pop()!.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())).join(', ')
        : `${areas.length} areas`,
    );
  }
  const lines = params.getAll('line');
  if (lines.length) {
    parts.push(lines.length <= 2 ? lines.map((l) => labels.lines?.get(l) ?? l).join(', ') : `${lines.length} lines`);
  }
  const st = params.getAll('st');
  if (st.length) parts.push(`${st.length} station${st.length > 1 ? 's' : ''}`);
  return parts.join(' · ') || 'Anywhere';
}
