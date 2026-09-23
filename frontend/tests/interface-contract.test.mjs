import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('forecast horizon uses the Lumen Select contract and keeps the native form field', () => {
  const overview = read('../src/features/forecast/Overview.astro');
  const dashboard = read('../src/lib/dashboard.ts');
  const styles = read('../src/styles/global.css');

  assert.match(overview, /import[^\n]*\bSelect\b[^\n]*@santi020k\/lumen-astro/);
  assert.match(overview, /<Label id="horizon-label" for="horizon"/);
  assert.match(overview, /<Select\s+id="horizon"\s+name="horizon"\s+aria-labelledby="horizon-label"\s+value="24"/);
  assert.match(overview, /data-horizon-select/);
  assert.doesNotMatch(overview, /NativeSelect/);
  assert.match(dashboard, /data-ui-select-option/);
  assert.doesNotMatch(dashboard, /horizon\.dispatchEvent\(new Event\('change',\s*\{\s*bubbles:\s*true/);
  assert.match(dashboard, /horizon\.dispatchEvent\(new Event\('change'\)\)/);
  assert.match(styles, /\.forecast-field \.ui-select__list[^\{]*\{[^}]*hsl\(var\(--surface\)\)/);
  assert.match(styles, /\.forecast-field \.ui-select__trigger:focus-visible/);
});

test('forecast rows are keyboard reachable in a bounded vertical scroller with sticky headings', () => {
  const table = read('../src/features/forecast/ForecastTable.astro');
  const styles = read('../src/styles/global.css');

  assert.match(table, /class="table-scroll forecast-table-scroll" tabindex="0" aria-label=/);
  assert.match(styles, /\.forecast-table-scroll\s*\{[^}]*height: min\([^}]*overflow-y: auto/s);
  assert.match(styles, /\.forecast-table-scroll \.ui-table-wrap\s*\{\s*overflow: visible;/);
  assert.match(styles, /\.forecast-table thead th\s*\{[^}]*position: sticky;[^}]*top: 0/s);
});

test('data quality view omits the eligibility row and its competition-only note', () => {
  const quality = read('../src/features/data-quality/render.ts');
  const panel = read('../src/features/data-quality/DataQualityPanel.astro');
  const preferences = read('../src/lib/preferences.mjs');
  const appShell = read('../src/components/AppShell.astro');
  const forecast = read('../src/features/forecast/render.ts');

  assert.doesNotMatch(quality, /competitionEligibility/);
  assert.doesNotMatch(panel, /competitionGateNote/);
  assert.doesNotMatch(appShell, /data-mode-badge|checkingMode/);
  assert.doesNotMatch(quality, /apiMode|health\?\.mode|manifest\.mode/);
  assert.doesNotMatch(forecast, /data-mode-badge|apiModeLabel|demoAlert/);
  assert.doesNotMatch(preferences, /checkingMode|demoMode|productionMode|offlineMode|apiMode|demoAlert/);
  assert.match(quality, /isSyntheticWeatherProvenance/);
  assert.match(forecast, /isSyntheticWeatherProvenance/);
  assert.match(preferences, /syntheticDataAlert: 'Погодные данные синтетические/);
  assert.match(preferences, /syntheticDataAlert: 'Ауа райы деректері синтетикалық/);
});
