import test from 'node:test';
import assert from 'node:assert/strict';
import {
  messages,
  readLocale,
  readTheme,
  resolveTheme,
  saveLocale,
  saveTheme,
  toggleTheme,
} from '../src/lib/preferences.mjs';

function makeStorage(entries = {}) {
  const values = new Map(Object.entries(entries));
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    values,
  };
}

test('Russian is the default and Kazakh has the same complete message keys', () => {
  assert.equal(readLocale(null), 'ru');
  assert.equal(messages.ru.navOverview, 'Обзор');
  assert.equal(messages.kk.navOverview, 'Шолу');
  assert.deepEqual(Object.keys(messages.kk).sort(), Object.keys(messages.ru).sort());
});

test('language preference is restored and saved without breaking when storage is unavailable', () => {
  const storage = makeStorage({ 'windline.locale': 'kk' });
  assert.equal(readLocale(storage), 'kk');
  saveLocale('ru', storage);
  assert.equal(storage.values.get('windline.locale'), 'ru');

  const blockedStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(readLocale(blockedStorage), 'ru');
  assert.doesNotThrow(() => saveLocale('kk', blockedStorage));
});

test('theme follows the system unless a saved light or dark preference overrides it', () => {
  const systemStorage = makeStorage();
  assert.equal(readTheme(systemStorage), 'system');
  assert.equal(resolveTheme(readTheme(systemStorage), true), 'dark');
  assert.equal(resolveTheme(readTheme(systemStorage), false), 'light');

  const savedStorage = makeStorage({ 'windline.theme': 'light' });
  assert.equal(resolveTheme(readTheme(savedStorage), true), 'light');
  saveTheme('dark', savedStorage);
  assert.equal(resolveTheme(readTheme(savedStorage), false), 'dark');
});

test('theme toggle stores an explicit preference derived from the current appearance', () => {
  const storage = makeStorage();
  assert.equal(toggleTheme('light', storage), 'dark');
  assert.equal(storage.values.get('windline.theme'), 'dark');
  assert.equal(toggleTheme('dark', storage), 'light');
  assert.equal(storage.values.get('windline.theme'), 'light');
});
