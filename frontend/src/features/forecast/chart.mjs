import { text } from '../../lib/preferences.mjs';
import { autoPercentDomain, percentAxisTicks } from './percent-axis.mjs';

const chartWidth = 920;
const chartHeight = 360;
const margin = { top: 44, right: 24, bottom: 48, left: 70 };
const plotWidth = chartWidth - margin.left - margin.right;
const plotHeight = chartHeight - margin.top - margin.bottom;

export function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function finiteValue(value) {
  return (typeof value === 'number' || (typeof value === 'string' && value.trim().length > 0))
    && Number.isFinite(Number(value));
}

function numberValue(value) {
  return finiteValue(value) ? Number(value) : null;
}

function forecastGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    if (!row || typeof row.turbine_id !== 'string' || !finiteValue(row.lead_hours)) continue;
    const points = groups.get(row.turbine_id) ?? [];
    points.push({
      lead: numberValue(row.lead_hours),
      p10: numberValue(row.p10),
      p50: numberValue(row.p50),
      p90: numberValue(row.p90),
    });
    groups.set(row.turbine_id, points);
  }

  return [...groups.entries()]
    .map(([id, points]) => [id, points.sort((left, right) => left.lead - right.lead)])
    .sort(([left], [right]) => left.localeCompare(right));
}

function labelForTurbine(id, locale) {
  if (id === 'turbine_1') return text(locale, 'turbineOne');
  if (id === 'turbine_2') return text(locale, 'turbineTwo');
  return id;
}

function linePath(points, key, x, y) {
  return points
    .filter((point) => finiteValue(point[key]))
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${x(point.lead).toFixed(2)} ${y(point[key]).toFixed(2)}`)
    .join(' ');
}

function quantileBand(points, x, y) {
  const complete = points.filter((point) => finiteValue(point.p10) && finiteValue(point.p90));
  if (complete.length < 2) return '';
  const upper = complete.map((point) => `${x(point.lead).toFixed(2)},${y(point.p90).toFixed(2)}`);
  const lower = complete.toReversed
    ? complete.toReversed().map((point) => `${x(point.lead).toFixed(2)},${y(point.p10).toFixed(2)}`)
    : [...complete].reverse().map((point) => `${x(point.lead).toFixed(2)},${y(point.p10).toFixed(2)}`);
  return [...upper, ...lower].join(' ');
}

function formatPercent(value, locale) {
  return `${new Intl.NumberFormat(locale === 'kk' ? 'kk-KZ' : 'ru-RU', { maximumFractionDigits: 2 }).format(value * 100)}%`;
}

export function renderForecastChart(rows, locale = 'ru', requestedDomain = null) {
  const groups = forecastGroups(Array.isArray(rows) ? rows : []);
  const chartPoints = groups.flatMap(([, points]) => points.flatMap((point) => [point.p10, point.p50, point.p90]))
    .filter(finiteValue);
  if (groups.length === 0 || chartPoints.length === 0) {
    return `<p class="chart-empty">${escapeHtml(text(locale, 'chartEmpty'))}</p>`;
  }

  const leads = groups.flatMap(([, points]) => points.map((point) => point.lead));
  const minLead = Math.min(...leads);
  const maxLead = Math.max(...leads);
  const leadRange = maxLead - minLead || 1;
  const [domainMin, domainMax] = Array.isArray(requestedDomain) && requestedDomain.length === 2
    ? requestedDomain
    : autoPercentDomain(rows);
  const x = (lead) => margin.left + ((lead - minLead) / leadRange) * plotWidth;
  const y = (value) => margin.top + ((domainMax - value) / (domainMax - domainMin)) * plotHeight;

  const grid = percentAxisTicks([domainMin, domainMax]).map((value) => {
    const yPosition = y(value);
    return `<g class="chart-grid-row"><line x1="${margin.left}" y1="${yPosition.toFixed(2)}" x2="${(chartWidth - margin.right).toFixed(2)}" y2="${yPosition.toFixed(2)}" /><text x="${margin.left - 12}" y="${(yPosition + 4).toFixed(2)}" text-anchor="end">${formatPercent(value, locale)}</text></g>`;
  }).join('');

  const allLeads = [...new Set(leads)].sort((left, right) => left - right);
  const tickIndexes = [...new Set([0, Math.floor((allLeads.length - 1) / 2), allLeads.length - 1])];
  const ticks = tickIndexes.map((index) => {
    const lead = allLeads[index];
    const xPosition = x(lead);
    return `<g class="chart-x-tick"><line x1="${xPosition.toFixed(2)}" y1="${(margin.top + plotHeight).toFixed(2)}" x2="${xPosition.toFixed(2)}" y2="${(margin.top + plotHeight + 5).toFixed(2)}" /><text x="${xPosition.toFixed(2)}" y="${(chartHeight - 16).toFixed(2)}" text-anchor="middle">${escapeHtml(text(locale, 'leadHours', { hours: lead }))}</text></g>`;
  }).join('');

  const series = groups.map(([id, points], index) => {
    const color = index % 2 === 0 ? 'hsl(var(--accent))' : 'hsl(var(--brand))';
    const label = escapeHtml(labelForTurbine(id, locale));
    const band = quantileBand(points, x, y);
    const lower = linePath(points, 'p10', x, y);
    const median = linePath(points, 'p50', x, y);
    const upper = linePath(points, 'p90', x, y);
    return `<g class="forecast-series" style="--series-color:${color}"><polygon class="uncertainty-band" points="${band}" aria-label="${label} · p10–p90" /><path class="forecast-bound" data-quantile="p10" d="${lower}" /><path class="forecast-median" data-quantile="p50" d="${median}" /><path class="forecast-bound" data-quantile="p90" d="${upper}" /><g class="series-label"><line x1="${margin.left + index * 160}" y1="21" x2="${margin.left + 22 + index * 160}" y2="21" /><text x="${margin.left + 30 + index * 160}" y="25">${label}</text></g></g>`;
  }).join('');

  return `<svg class="forecast-plot" viewBox="0 0 ${chartWidth} ${chartHeight}" role="img" aria-label="${escapeHtml(text(locale, 'chartSvgLabel'))}"><title>${escapeHtml(text(locale, 'chartSvgTitle'))}</title><desc>${escapeHtml(text(locale, 'chartSvgDescription'))}</desc><defs><clipPath id="forecast-plot-clip"><rect x="${margin.left}" y="${margin.top}" width="${plotWidth}" height="${plotHeight}" /></clipPath></defs><g class="chart-grid">${grid}</g><line class="chart-axis" x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${margin.top + plotHeight}" /><line class="chart-axis" x1="${margin.left}" y1="${margin.top + plotHeight}" x2="${chartWidth - margin.right}" y2="${margin.top + plotHeight}" />${ticks}<text class="axis-title" x="16" y="${chartHeight / 2}" text-anchor="middle" transform="rotate(-90 16 ${chartHeight / 2})">${escapeHtml(text(locale, 'normalizedOutput'))}</text><g class="forecast-series-layer" clip-path="url(#forecast-plot-clip)">${series}</g></svg>`;
}
