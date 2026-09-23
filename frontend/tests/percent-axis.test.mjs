import assert from 'node:assert/strict';
import test from 'node:test';
import {
  autoPercentDomain,
  createPercentAxisState,
  percentAxisTicks,
  reducePercentAxisState,
  resolvePercentAxisDomain,
} from '../src/features/forecast/percent-axis.mjs';

const rows = [
  { p10: 0.2, p50: 0.45, p90: 0.65 },
  { p10: 0.25, p50: 0.5, p90: 0.7 },
];

test('automatic domain includes every quantile with proportional padding', () => {
  assert.deepEqual(autoPercentDomain(rows), [0.14, 0.76]);
  assert.deepEqual(autoPercentDomain([{ p10: 0, p50: 0.4, p90: 1 }]), [0, 1]);
});

test('automatic domain handles flat, empty, and invalid input safely', () => {
  assert.deepEqual(autoPercentDomain([{ p10: 0.5, p50: 0.5, p90: 0.5 }]), [0.49, 0.51]);
  assert.deepEqual(autoPercentDomain([{ p10: 0, p50: 0, p90: 0 }]), [0, 0.02]);
  assert.deepEqual(autoPercentDomain([]), [0, 1]);
  assert.deepEqual(autoPercentDomain([{ p10: 'bad', p50: null, p90: Number.NaN }]), [0, 1]);
});

test('percent ticks are aligned to their value and retain the precision the step requires', () => {
  assert.deepEqual(percentAxisTicks([0.1, 0.5]), [0.1, 0.2, 0.3, 0.4, 0.5]);
  assert.deepEqual(percentAxisTicks([0, 0.02]), [0, 0.005, 0.01, 0.015, 0.02]);
});

test('manual zoom stays centered, reaches under 100 points, and respects a 2-point minimum span', () => {
  const start = { mode: 'manual', domain: [0.2, 0.8] };
  const zoomIn = reducePercentAxisState(start, 'zoom-in', rows);
  assert.deepEqual(zoomIn.domain, [0.26, 0.74]);
  assert.deepEqual(reducePercentAxisState({ mode: 'manual', domain: [0, 0.4] }, 'zoom-in', rows).domain, [0.04, 0.36]);

  let narrow = { mode: 'manual', domain: [0.49, 0.51] };
  for (let index = 0; index < 20; index += 1) narrow = reducePercentAxisState(narrow, 'zoom-in', rows);
  assert.deepEqual(narrow.domain, [0.49, 0.51]);

  let wide = { mode: 'manual', domain: [0, 1] };
  for (let index = 0; index < 20; index += 1) wide = reducePercentAxisState(wide, 'zoom-out', rows);
  assert.deepEqual(wide.domain, [-1, 2]);
});

test('zoom-out may pass 0–100 while still respecting its extended finite viewport bounds', () => {
  const first = reducePercentAxisState({ mode: 'full', domain: [0, 1] }, 'zoom-out', rows);
  assert.deepEqual(first.domain, [-0.125, 1.125]);
  const narrowed = reducePercentAxisState({ mode: 'manual', domain: [-0.2, 1.2] }, 'zoom-in', rows);
  assert.deepEqual(narrowed.domain, [-0.06, 1.06]);

  let wide = { mode: 'full', domain: [0, 1] };
  for (let index = 0; index < 30; index += 1) wide = reducePercentAxisState(wide, 'zoom-out', rows);
  assert.deepEqual(wide.domain, [-1, 2]);
});

test('vertical panning preserves scale, supports both directions, and stops at viewport edges', () => {
  const domain = [0.2, 0.8];
  const up = reducePercentAxisState({ mode: 'manual', domain }, 'pan-up', rows);
  const down = reducePercentAxisState({ mode: 'manual', domain }, 'pan-down', rows);
  assert.deepEqual(up.domain, [0.26, 0.86]);
  assert.deepEqual(down.domain, [0.14, 0.74]);
  assert.ok(Math.abs((up.domain[1] - up.domain[0]) - (domain[1] - domain[0])) < 1e-12);

  let atTop = { mode: 'manual', domain: [1, 2] };
  for (let index = 0; index < 10; index += 1) atTop = reducePercentAxisState(atTop, 'pan-up', rows);
  assert.deepEqual(atTop.domain, [1, 2]);
  let atBottom = { mode: 'manual', domain: [-1, 0] };
  for (let index = 0; index < 10; index += 1) atBottom = reducePercentAxisState(atBottom, 'pan-down', rows);
  assert.deepEqual(atBottom.domain, [-1, 0]);
});

test('auto and full-range resets are explicit; locale rerenders do not affect pure axis state', () => {
  const initial = createPercentAxisState();
  assert.deepEqual(initial, { mode: 'auto', domain: null });
  assert.deepEqual(resolvePercentAxisDomain(initial, rows), [0.14, 0.76]);
  assert.deepEqual(reducePercentAxisState({ mode: 'manual', domain: [0.3, 0.5] }, 'auto', rows), initial);
  assert.deepEqual(reducePercentAxisState({ mode: 'auto', domain: null }, 'full', rows), { mode: 'full', domain: [0, 1] });
});
