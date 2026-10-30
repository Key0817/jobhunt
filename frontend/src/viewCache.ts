import { useState } from 'react';
import type { SetStateAction } from 'react';
// Memory only: never persist private API responses or share them between users.
const cache = new Map<string, unknown>();
let generation = 0;
export const cacheGeneration = () => generation;
export function getCached<T>(key: string): T | undefined { return cache.get(key) as T | undefined; }
export function setCached<T>(key: string, value: T) {
  if (!cache.has(key) && cache.size >= 100) cache.delete(cache.keys().next().value!);
  cache.set(key, value);
}
const resetListeners = new Set<() => void>();
export function onCacheReset(callback: () => void) { resetListeners.add(callback); return () => { resetListeners.delete(callback); }; }
export function clearViewCache() { generation += 1; cache.clear(); resetListeners.forEach(listener => listener()); }
export function useViewState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => getCached<T>(key) ?? initial);
  function update(next: SetStateAction<T>) {
    setValue(current => {
      const result = typeof next === 'function' ? (next as (value: T) => T)(current) : next;
      setCached(key, result);
      return result;
    });
  }
  return [value, update] as const;
}

type CVChange = { id: string; job_id?: string; status?: string; approved?: boolean };
const cvListeners = new Set<(change: CVChange) => void>();
export function subscribeCVChanges(callback: (change: CVChange) => void) {
  cvListeners.add(callback); return () => { cvListeners.delete(callback); };
}
export function publishCVChange(change: CVChange) {
  for (const [key, value] of cache) {
    if ((key === 'cvs' || key.startsWith('job-cvs:')) && Array.isArray(value)) {
      cache.set(key, value.map(cv => cv.id === change.id ? { ...cv, ...change } : cv));
    }
  }
  cvListeners.forEach(listener => listener(change));
}
