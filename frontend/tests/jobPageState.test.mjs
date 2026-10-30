import test from 'node:test';
import assert from 'node:assert/strict';
import { updateJobPage, matchesPageFilters } from '../src/jobPageState.ts';

const job = { id: 'a', status: 'encontrada', score: 80, detected_at: '2026-10-30T12:00:00Z', source: 'getonboard', origins: [], remote: true, location: 'CR' };
const moved = { ...job, status: 'aplicable' };
const page = { items: [job], total: 1, page: 1, page_size: 25, total_pages: 1 };

test('unfiltered offers update in place without changing count or order', () => {
  const updated = updateJobPage(page, null, job, moved);
  assert.equal(updated.total, 1);
  assert.equal(updated.items[0].status, 'aplicable');
});
test('source column removes the card and destination inserts it immediately', () => {
  assert.equal(updateJobPage(page, 'encontrada', job, moved).total, 0);
  assert.deepEqual(updateJobPage(page, 'encontrada', job, moved).items, []);
  const destination = updateJobPage({ ...page, items: [], total: 0 }, 'aplicable', job, moved);
  assert.equal(destination.items[0].id, 'a');
  assert.equal(destination.total, 1);
});
test('unrelated columns retain their existing state', () => {
  assert.equal(updateJobPage(page, 'entrevista', job, moved), page);
});
test('rollback restores the previous status and total', () => {
  const removed = updateJobPage(page, 'encontrada', job, moved);
  const restored = updateJobPage(removed, 'encontrada', moved, job);
  assert.equal(restored.total, 1);
  assert.equal(restored.items[0].status, 'encontrada');
});
test('first-page insertion respects score ordering and page size', () => {
  const existing = { ...moved, id: 'b', score: 90 };
  const result = updateJobPage({ ...page, items: [existing], page_size: 1 }, 'aplicable', job, moved);
  assert.equal(result.items[0].id, 'b');
  assert.equal(result.total, 2);
  assert.equal(result.total_pages, 2);
});
test('destination pages after page 1 do not insert a card in the wrong position', () => {
  const result = updateJobPage({ ...page, page: 2, total: 26, items: [] }, 'aplicable', job, moved);
  assert.equal(result.total, 27);
  assert.deepEqual(result.items, []);
});
test('source aliases, minimum score and modality apply to optimistic updates', () => {
  assert.equal(matchesPageFilters(job, new URLSearchParams('source=lever')), false);
  assert.equal(matchesPageFilters({ ...job, origins: [{ source: 'linkedin_email' }] }, new URLSearchParams('source=email')), true);
  assert.equal(matchesPageFilters(job, new URLSearchParams('min_score=90')), false);
  assert.equal(matchesPageFilters(job, new URLSearchParams('modality=onsite')), false);
  assert.equal(matchesPageFilters(job, new URLSearchParams('source=getonboard&modality=remote')), true);
});
