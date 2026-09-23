const MIN_DOMAIN_SPAN = 0.02;
const DATA_MIN = 0;
const DATA_MAX = 1;
const VIEWPORT_MIN = -1;
const VIEWPORT_MAX = 2;
const QUANTILES = ['p10', 'p50', 'p90'];

function rounded(value) {
  return Math.round(value * 1e12) / 1e12;
}

function fraction(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && value.trim() === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function normalizeDomain(domain, fallback = [DATA_MIN, DATA_MAX]) {
  if (!Array.isArray(domain) || domain.length !== 2) return fallback;
  const low = fraction(domain[0]);
  const high = fraction(domain[1]);
  if (low === null || high === null || high - low < MIN_DOMAIN_SPAN) return fallback;
  const normalizedLow = Math.max(VIEWPORT_MIN, Math.min(VIEWPORT_MAX, low));
  const normalizedHigh = Math.max(VIEWPORT_MIN, Math.min(VIEWPORT_MAX, high));
  if (normalizedHigh - normalizedLow < MIN_DOMAIN_SPAN) return fallback;
  return [rounded(normalizedLow), rounded(normalizedHigh)];
}

export function autoPercentDomain(rows) {
  const values = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row !== 'object') continue;
    for (const quantile of QUANTILES) {
      const value = fraction(row[quantile]);
      if (value !== null) values.push(Math.max(DATA_MIN, Math.min(DATA_MAX, value)));
    }
  }
  if (values.length === 0) return [DATA_MIN, DATA_MAX];

  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const padding = Math.max((maximum - minimum) * 0.12, MIN_DOMAIN_SPAN / 2);
  let low = Math.max(DATA_MIN, minimum - padding);
  let high = Math.min(DATA_MAX, maximum + padding);
  if (high - low < MIN_DOMAIN_SPAN) {
    const center = (minimum + maximum) / 2;
    low = Math.max(DATA_MIN, center - MIN_DOMAIN_SPAN / 2);
    high = Math.min(DATA_MAX, low + MIN_DOMAIN_SPAN);
    low = Math.max(DATA_MIN, high - MIN_DOMAIN_SPAN);
  }
  return [rounded(low), rounded(high)];
}

export function createPercentAxisState() {
  return { mode: 'auto', domain: null };
}

export function resolvePercentAxisDomain(state, rows) {
  if (!state || state.mode === 'auto') return autoPercentDomain(rows);
  if (state.mode === 'full') return [DATA_MIN, DATA_MAX];
  return normalizeDomain(state.domain, autoPercentDomain(rows));
}

export function reducePercentAxisState(state, action, rows) {
  if (action === 'auto') return createPercentAxisState();
  if (action === 'full') return { mode: 'full', domain: [DATA_MIN, DATA_MAX] };
  if (!['zoom-in', 'zoom-out', 'pan-up', 'pan-down'].includes(action)) return state ?? createPercentAxisState();

  const [low, high] = resolvePercentAxisDomain(state, rows);
  const currentSpan = high - low;
  const center = (low + high) / 2;
  let nextLow;
  let nextHigh;
  if (action === 'zoom-in' || action === 'zoom-out') {
    const maxSpan = VIEWPORT_MAX - VIEWPORT_MIN;
    const span = action === 'zoom-in'
      ? Math.max(MIN_DOMAIN_SPAN, currentSpan * 0.8)
      : Math.min(maxSpan, currentSpan * 1.25);
    nextLow = center - span / 2;
    nextHigh = center + span / 2;
  } else {
    const offset = Math.max(MIN_DOMAIN_SPAN, currentSpan * 0.1) * (action === 'pan-up' ? 1 : -1);
    nextLow = low + offset;
    nextHigh = high + offset;
  }
  if (nextLow < VIEWPORT_MIN) {
    nextHigh += VIEWPORT_MIN - nextLow;
    nextLow = VIEWPORT_MIN;
  }
  if (nextHigh > VIEWPORT_MAX) {
    nextLow -= nextHigh - VIEWPORT_MAX;
    nextHigh = VIEWPORT_MAX;
  }
  nextLow = Math.max(VIEWPORT_MIN, nextLow);
  nextHigh = Math.min(VIEWPORT_MAX, nextHigh);
  nextLow = rounded(nextLow);
  nextHigh = rounded(nextHigh);
  if (nextHigh - nextLow < MIN_DOMAIN_SPAN) {
    nextHigh = Math.min(VIEWPORT_MAX, nextLow + MIN_DOMAIN_SPAN);
    nextLow = Math.max(VIEWPORT_MIN, nextHigh - MIN_DOMAIN_SPAN);
  }
  const nextDomain = [rounded(nextLow), rounded(nextHigh)];
  return nextDomain[0] === DATA_MIN && nextDomain[1] === DATA_MAX
    ? { mode: 'full', domain: nextDomain }
    : { mode: 'manual', domain: nextDomain };
}

function niceStep(rawStep) {
  const safeStep = Number.isFinite(rawStep) && rawStep > 0 ? rawStep : MIN_DOMAIN_SPAN / 4;
  const exponent = Math.floor(Math.log10(safeStep));
  const magnitude = 10 ** exponent;
  const normalized = safeStep / magnitude;
  const choices = [1, 2, 2.5, 5, 10];
  const choice = choices.find((candidate) => candidate + 1e-9 >= normalized) ?? 10;
  return choice * magnitude;
}

export function percentAxisTicks(domain, targetIntervals = 4) {
  const [low, high] = normalizeDomain(domain);
  const intervalCount = Number.isInteger(targetIntervals) && targetIntervals > 0 ? targetIntervals : 4;
  const step = niceStep((high - low) / intervalCount);
  const start = Math.ceil((low - step * 1e-9) / step) * step;
  const ticks = [];
  for (let value = start, index = 0; value <= high + step * 1e-9 && index < 12; value = start + (++index) * step) {
    ticks.push(rounded(Math.max(low, Math.min(high, value))));
  }
  return ticks.length ? ticks : [rounded((low + high) / 2)];
}
