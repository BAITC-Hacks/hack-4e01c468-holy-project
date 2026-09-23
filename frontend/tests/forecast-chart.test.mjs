import assert from 'node:assert/strict';
import test from 'node:test';
import { escapeHtml, renderForecastChart } from '../src/features/forecast/chart.mjs';

const rows = [
  { turbine_id: 'turbine_1', lead_hours: 0, p10: 0.02, p50: 0.06, p90: 0.13 },
  { turbine_id: 'turbine_1', lead_hours: 1, p10: 0.04, p50: 0.08, p90: 0.18 },
  { turbine_id: 'turbine_2', lead_hours: 0, p10: 0.01, p50: 0.05, p90: 0.11 },
  { turbine_id: 'turbine_2', lead_hours: 1, p10: 0.03, p50: 0.07, p90: 0.16 },
];

test('plots both turbines with a p10–p90 uncertainty band and median line', () => {
  const svg = renderForecastChart(rows);

  assert.equal((svg.match(/class="uncertainty-band/g) ?? []).length, 2);
  assert.equal((svg.match(/class="forecast-median/g) ?? []).length, 2);
  assert.match(svg, /Турбина 01/);
  assert.match(svg, /Турбина 02/);
  assert.match(svg, /Почасовой прогноз/);
});

test('localizes chart descriptions and empty state into Kazakh', () => {
  const svg = renderForecastChart(rows, 'kk');
  assert.match(svg, /сағаттық болжам/);
  assert.match(renderForecastChart([], 'kk'), /болжам деректері жоқ/);
});

test('plots numeric CSV values as normalized quantiles', () => {
  const csvRows = rows.map((row) => Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, typeof value === 'number' ? String(value) : value]),
  ));

  assert.equal((renderForecastChart(csvRows).match(/class="uncertainty-band/g) ?? []).length, 2);
});

test('escapes backend text before it is placed in an HTML surface', () => {
  assert.equal(escapeHtml(`<svg onload="alert('x')">&`), '&lt;svg onload=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;');
});
