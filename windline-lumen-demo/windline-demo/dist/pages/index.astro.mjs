import { c as createComponent, m as maybeRenderHead, a as addAttribute, s as spreadAttributes, r as renderSlot, b as renderTemplate, d as createAstro, e as renderComponent, F as Fragment, f as renderHead, g as renderScript } from '../chunks/astro/server_B7EGbH0l.mjs';
import 'piccolore';
import 'html-escaper';
/* empty css                                 */
import { resolveLumenAstroProps, getLumenChartCategories, hasLumenChartData, alignLumenChartSeries, getLumenChartDomain, createLumenLineGeometry, getLumenChartTicks, scaleLumenChartValue, getLumenChartToneClassName, resolveLumenChartTone } from '@santi020k/lumen-core';
import 'clsx';
export { renderers } from '../renderers.mjs';

const $$Astro$b = createAstro();
const $$Alert = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$b, $$props, $$slots);
  Astro2.self = $$Alert;
  const {
    class: classProp = "",
    className = "",
    glass = false,
    variant = "default",
    ...rest
  } = Astro2.props;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-alert",
    variant === "destructive" && "ui-alert--destructive",
    variant === "success" && "ui-alert--success",
    variant === "warning" && "ui-alert--warning",
    glass && "ui-alert--glass",
    glass === "subtle" && "ui-glass-subtle",
    glass === "strong" && "ui-glass-strong"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<aside${addAttribute(variant, "data-variant")}${addAttribute(classList, "class:list")}${spreadAttributes(passthrough)}> ${renderSlot($$result, $$slots["default"])} </aside>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Alert.astro", void 0);

const $$Astro$a = createAstro();
const $$Badge = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$a, $$props, $$slots);
  Astro2.self = $$Badge;
  const {
    class: classProp = "",
    className = "",
    variant = "default",
    ...rest
  } = Astro2.props;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-badge",
    variant === "default" && "ui-badge--default",
    variant === "secondary" && "ui-badge--secondary",
    variant === "outline" && "ui-badge--outline",
    variant === "destructive" && "ui-badge--destructive",
    variant === "success" && "ui-badge--success",
    variant === "warning" && "ui-badge--warning"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<span${addAttribute(variant, "data-variant")}${addAttribute(classList, "class:list")}${spreadAttributes(passthrough)}> ${renderSlot($$result, $$slots["default"])} </span>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Badge.astro", void 0);

const $$Astro$9 = createAstro();
const $$Chart = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$9, $$props, $$slots);
  Astro2.self = $$Chart;
  const {
    caption,
    class: classProp = "",
    className = "",
    description,
    glass = false,
    heading,
    presentation = "default",
    value,
    ...rest
  } = Astro2.props;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-chart",
    presentation === "bare" && "ui-chart--bare",
    glass && "ui-chart--glass",
    glass === "subtle" && "ui-glass-subtle",
    glass === "strong" && "ui-glass-strong"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<figure${addAttribute(classList, "class:list")}${spreadAttributes(passthrough)}> ${(heading || description || value || Astro2.slots.has("header")) && renderTemplate`<header> ${renderSlot($$result, $$slots["header"], renderTemplate` <div class="ui-chart__heading"> ${heading && renderTemplate`<h3>${heading}</h3>`} ${description && renderTemplate`<p>${description}</p>`} </div> ${value && renderTemplate`<strong data-ui-chart-value>${value}</strong>`} `)} ${renderSlot($$result, $$slots["actions"])} </header>`} ${renderSlot($$result, $$slots["default"])} ${caption && renderTemplate`<figcaption>${caption}</figcaption>`} </figure>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Chart.astro", void 0);

const $$Astro$8 = createAstro();
const $$Button = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$8, $$props, $$slots);
  Astro2.self = $$Button;
  const {
    class: classProp = "",
    className = "",
    disabled,
    loading = false,
    size = "default",
    type = "button",
    variant = "default",
    ...rest
  } = Astro2.props;
  const isDisabled = Boolean(disabled) || loading;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-button",
    variant === "default" && "ui-button--default",
    variant === "destructive" && "ui-button--destructive",
    variant === "ghost" && "ui-button--ghost",
    variant === "link" && "ui-button--link",
    variant === "outline" && "ui-button--outline",
    variant === "secondary" && "ui-button--secondary",
    size === "default" && "ui-button--default-size",
    size === "sm" && "ui-button--sm",
    size === "lg" && "ui-button--lg",
    size === "icon" && "ui-button--icon",
    isDisabled && "ui-button--disabled",
    loading && "ui-button--loading"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<button${addAttribute(loading || void 0, "aria-busy")}${addAttribute(classList, "class:list")} data-slot="button"${addAttribute(isDisabled || void 0, "disabled")}${addAttribute(type, "type")}${spreadAttributes(passthrough)}> ${loading && renderTemplate`<span aria-hidden="true" class="ui-spinner"></span>`} ${renderSlot($$result, $$slots["default"])} </button>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Button.astro", void 0);

const $$Astro$7 = createAstro();
const $$Card = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$7, $$props, $$slots);
  Astro2.self = $$Card;
  const {
    as: Tag = "div",
    class: classProp = "",
    className = "",
    glass = false,
    variant = "default",
    ...rest
  } = Astro2.props;
  const isGlass = glass || variant === "glass";
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-card",
    variant === "unstyled" && "ui-card--unstyled",
    variant === "muted" && "ui-card--muted",
    variant === "interactive" && "ui-card--interactive",
    isGlass && "ui-card--glass",
    glass === "subtle" && "ui-glass-subtle",
    glass === "strong" && "ui-glass-strong"
  ], classProp, className);
  return renderTemplate`${renderComponent($$result, "Tag", Tag, { "class:list": classList, "data-slot": "card", "data-variant": variant, ...passthrough }, { "default": ($$result2) => renderTemplate` ${renderSlot($$result2, $$slots["default"])} ` })}`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Card.astro", void 0);

const $$Astro$6 = createAstro();
const $$Field = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$6, $$props, $$slots);
  Astro2.self = $$Field;
  const {
    "aria-describedby": ariaDescribedby,
    class: classProp = "",
    className = "",
    controlId,
    describedBy,
    glass = false,
    ...rest
  } = Astro2.props;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-field",
    glass && "ui-field--glass",
    glass === "subtle" && "ui-glass-subtle",
    glass === "strong" && "ui-glass-strong"
  ], classProp, className);
  const fieldDescribedBy = [ariaDescribedby, describedBy].filter(Boolean).join(" ") || void 0;
  return renderTemplate`${maybeRenderHead()}<div${addAttribute(ariaDescribedby, "aria-describedby")}${addAttribute(classList, "class:list")} data-ui-field${addAttribute(controlId, "data-ui-field-control")}${addAttribute(fieldDescribedBy, "data-ui-field-describedby")}${spreadAttributes(passthrough)}> ${renderSlot($$result, $$slots["default"])} </div>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Field.astro", void 0);

const $$Astro$5 = createAstro();
const $$Input = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$5, $$props, $$slots);
  Astro2.self = $$Input;
  const {
    class: classProp = "",
    className = "",
    size,
    type = "text",
    visualSize,
    ...rest
  } = Astro2.props;
  const legacyVisualSize = size === "default" || size === "lg" || size === "sm" ? size : void 0;
  const resolvedVisualSize = visualSize ?? legacyVisualSize ?? "default";
  const nativeSize = legacyVisualSize ? void 0 : size;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-input",
    resolvedVisualSize === "sm" && "ui-input--sm",
    resolvedVisualSize === "lg" && "ui-input--lg"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<input${addAttribute(classList, "class:list")}${addAttribute(nativeSize, "size")}${addAttribute(type, "type")}${spreadAttributes(passthrough)}>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Input.astro", void 0);

const $$Astro$4 = createAstro();
const $$Label = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$4, $$props, $$slots);
  Astro2.self = $$Label;
  const { class: classProp = "", className = "", ...rest } = Astro2.props;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-label"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<label${addAttribute(classList, "class:list")}${spreadAttributes(passthrough)}> ${renderSlot($$result, $$slots["default"])} </label>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Label.astro", void 0);

const $$Astro$3 = createAstro();
const $$LineChart = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$3, $$props, $$slots);
  Astro2.self = $$LineChart;
  const {
    area = false,
    caption,
    class: classProp = "",
    className = "",
    description,
    emptyLabel = "No chart data available.",
    formatCategory = String,
    formatValue = String,
    glass = false,
    heading,
    markers = "auto",
    referenceValue,
    series,
    showLegend = series.length > 1,
    showTable = true,
    ...rest
  } = Astro2.props;
  const width = 640;
  const height = 320;
  const padding = 44;
  const categories = getLumenChartCategories(series);
  const hasData = hasLumenChartData(series);
  const alignedSeries = series.map((item) => alignLumenChartSeries(item, categories));
  const domain = getLumenChartDomain([
    ...alignedSeries.flatMap((item) => item.data.map((datum) => datum.y)),
    referenceValue ?? null
  ], false);
  const geometries = alignedSeries.map((item) => createLumenLineGeometry(item.data, {
    domain,
    height,
    includeZero: false,
    padding,
    width
  }));
  const ticks = getLumenChartTicks(domain);
  const labelStep = Math.max(1, Math.ceil(categories.length / 8));
  let markerStep = 1;
  if (markers === false || markers === "none") markerStep = Number.POSITIVE_INFINITY;
  else if (typeof markers === "number") markerStep = Math.max(1, Math.round(markers));
  else if (markers === "auto") markerStep = Math.max(1, Math.ceil(categories.length / 24));
  const valueFor = (item, category) => item.data.find((datum) => datum.x === category);
  const categoryLabelFor = (category) => series.flatMap((item) => item.data).find((datum) => datum.x === category)?.xLabel ?? formatCategory(category);
  const referenceY = referenceValue === void 0 ? void 0 : scaleLumenChartValue(referenceValue, domain, height - padding, padding);
  return renderTemplate`${renderComponent($$result, "Chart", $$Chart, { "class:list": ["ui-line-chart", classProp, className], "caption": caption, "description": description, "glass": glass, "heading": heading, ...rest }, { "default": ($$result2) => renderTemplate`${showLegend && hasData && renderTemplate`${maybeRenderHead()}<ul class="ui-chart__legend" aria-label="Chart legend"> ${series.map((item, index) => renderTemplate`<li${addAttribute(getLumenChartToneClassName(item.tone, index), "class")}> <span aria-hidden="true"></span> ${item.label} </li>`)} </ul>`}${!hasData && renderTemplate`<p class="ui-chart__empty" role="status">${emptyLabel}</p>`}<div class="ui-chart__plot"${addAttribute(!hasData, "hidden")}> <svg aria-hidden="true" preserveAspectRatio="xMidYMid meet"${addAttribute(`0 0 ${width} ${height}`, "viewBox")}> <g class="ui-chart__grid"> ${ticks.map((tick) => {
    const y = scaleLumenChartValue(
      tick,
      domain,
      height - padding,
      padding
    );
    return renderTemplate`${renderComponent($$result2, "Fragment", Fragment, {}, { "default": ($$result3) => renderTemplate` <line${addAttribute(padding, "x1")}${addAttribute(width - padding, "x2")}${addAttribute(y, "y1")}${addAttribute(y, "y2")}></line> <text${addAttribute(padding - 8, "x")}${addAttribute(y, "y")}> ${formatValue(tick)} </text> ` })}`;
  })} </g> <g class="ui-chart__axis-labels"> ${categories.map((category, index) => {
    if (index % labelStep !== 0 && index !== categories.length - 1)
      return null;
    const denominator = Math.max(1, categories.length - 1);
    const x = padding + index / denominator * (width - padding * 2);
    return renderTemplate`<text text-anchor="middle"${addAttribute(x, "x")}${addAttribute(height - 14, "y")}> ${categoryLabelFor(category)} </text>`;
  })} </g> ${referenceY !== void 0 && renderTemplate`<line class="ui-chart__reference"${addAttribute(padding, "x1")}${addAttribute(width - padding, "x2")}${addAttribute(referenceY, "y1")}${addAttribute(referenceY, "y2")}></line>`} ${geometries.map((geometry, index) => {
    const item = series[index];
    if (!item) return null;
    const tone = resolveLumenChartTone(item.tone, index);
    return renderTemplate`<g${addAttribute(["ui-line-chart__series", getLumenChartToneClassName(tone)], "class:list")}> ${area && geometry.areaPaths.map((path) => renderTemplate`<path class="ui-line-chart__area"${addAttribute(path, "d")}></path>`)} <path class="ui-line-chart__line"${addAttribute(geometry.path, "d")}></path> ${Number.isFinite(markerStep) && geometry.points.map((point, pointIndex) => pointIndex % markerStep === 0 && renderTemplate`<circle class="ui-line-chart__point"${addAttribute(point.xCoordinate, "cx")}${addAttribute(point.yCoordinate, "cy")} r="3"> <title>
                      ${`${point.xLabel ?? formatCategory(point.x)} \xB7 ${item.label}: ${formatValue(point.y ?? 0)}`}
                    </title> </circle>`)} </g>`;
  })} </svg> </div> ${showTable && hasData && renderTemplate`<details class="ui-chart__data"> <summary>View chart data</summary> <div> <table> <thead> <tr> <th scope="col">Category</th> ${series.map((item) => renderTemplate`<th scope="col">${item.label}</th>`)} </tr> </thead> <tbody> ${categories.map((category) => renderTemplate`<tr> <th scope="row">${categoryLabelFor(category)}</th> ${series.map((item) => {
    const datum = valueFor(item, category);
    return renderTemplate`<td> ${datum?.label ?? (datum?.y === null || datum === void 0 ? "Not available" : formatValue(datum.y))} </td>`;
  })} </tr>`)} </tbody> </table> </div> </details>`}` })}`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/LineChart.astro", void 0);

const $$Astro$2 = createAstro();
const $$NativeSelect = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$2, $$props, $$slots);
  Astro2.self = $$NativeSelect;
  const {
    class: classProp = "",
    className = "",
    options = [],
    placeholder,
    size,
    visualSize,
    ...rest
  } = Astro2.props;
  const hasValue = Object.hasOwn(rest, "value");
  const normalizedOptions = options.map((option) => typeof option === "string" ? { label: option, value: option } : option);
  const legacyVisualSize = size === "default" || size === "lg" || size === "sm" ? size : void 0;
  const resolvedVisualSize = visualSize ?? legacyVisualSize ?? "default";
  const nativeSize = legacyVisualSize ? void 0 : size;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-select",
    resolvedVisualSize === "sm" && "ui-select--sm",
    resolvedVisualSize === "lg" && "ui-select--lg"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<select${addAttribute(classList, "class:list")}${addAttribute(nativeSize, "size")}${spreadAttributes(passthrough)}> ${placeholder && renderTemplate`<option disabled${addAttribute(!hasValue, "selected")} value="">${placeholder}</option>`} ${renderSlot($$result, $$slots["default"])} ${normalizedOptions.map((option) => renderTemplate`<option${addAttribute(option.disabled, "disabled")}${addAttribute(option.value, "value")}>${option.label}</option>`)} </select>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/NativeSelect.astro", void 0);

const $$Astro$1 = createAstro();
const $$Sidebar = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro$1, $$props, $$slots);
  Astro2.self = $$Sidebar;
  const {
    class: classProp = "",
    className = "",
    glass = false,
    variant = "default",
    ...rest
  } = Astro2.props;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-sidebar",
    variant === "unstyled" && "ui-sidebar--unstyled",
    glass && "ui-sidebar--glass",
    glass === "subtle" && "ui-glass-subtle",
    glass === "strong" && "ui-glass-strong"
  ], classProp, className);
  return renderTemplate`${maybeRenderHead()}<aside data-slot="sidebar"${addAttribute(glass ? "glass" : "default", "data-surface")}${addAttribute(variant, "data-variant")}${addAttribute(classList, "class:list")}${spreadAttributes(passthrough)}> ${renderSlot($$result, $$slots["default"])} </aside>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Sidebar.astro", void 0);

const $$Astro = createAstro();
const $$Stat = createComponent(($$result, $$props, $$slots) => {
  const Astro2 = $$result.createAstro($$Astro, $$props, $$slots);
  Astro2.self = $$Stat;
  const {
    as: Tag = "div",
    class: classProp = "",
    className = "",
    label,
    value,
    variant = "default",
    ...rest
  } = Astro2.props;
  const { classList, passthrough } = resolveLumenAstroProps(rest, [
    "ui-stat",
    variant !== "default" && `ui-stat--${variant}`
  ], classProp, className);
  return renderTemplate`${renderComponent($$result, "Tag", Tag, { "class:list": classList, "data-slot": "stat", "data-variant": variant, ...passthrough }, { "default": ($$result2) => renderTemplate`${label && renderTemplate`${maybeRenderHead()}<div class="ui-stat-label" data-slot="stat-label">${label}</div>`}${value && renderTemplate`<div class="ui-stat-value" data-slot="stat-value">${value}</div>`}${renderSlot($$result2, $$slots["default"])} ` })}`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/node_modules/@santi020k/lumen-astro/components/Stat.astro", void 0);

const $$Index = createComponent(($$result, $$props, $$slots) => {
  const dates = ["01 \u0444\u0435\u0432 00:00", "01 \u0444\u0435\u0432 06:00", "01 \u0444\u0435\u0432 12:00", "01 \u0444\u0435\u0432 18:00", "02 \u0444\u0435\u0432 00:00", "02 \u0444\u0435\u0432 06:00", "02 \u0444\u0435\u0432 12:00", "02 \u0444\u0435\u0432 18:00"];
  const series = [
    { id: "t1", label: "\u0422\u0443\u0440\u0431\u0438\u043D\u0430 01", data: dates.map((x, i) => ({ x, y: [18, 21, 25, 24, 29, 34, 32, 37][i] })) },
    { id: "t2", label: "\u0422\u0443\u0440\u0431\u0438\u043D\u0430 02", data: dates.map((x, i) => ({ x, y: [15, 17, 20, 19, 23, 27, 26, 30][i] })) }
  ];
  return renderTemplate`<html lang="ru" data-theme="light"> <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#102a32"><title>Windline — Центр прогнозирования</title><meta name="description" content="Демонстрационный интерфейс прогнозирования выработки ветряной электростанции">${renderHead()}</head> <body> <div class="app-shell"> ${renderComponent($$result, "Sidebar", $$Sidebar, { "class": "side-nav", "aria-label": "\u041E\u0441\u043D\u043E\u0432\u043D\u0430\u044F \u043D\u0430\u0432\u0438\u0433\u0430\u0446\u0438\u044F" }, { "default": ($$result2) => renderTemplate` <a class="brand" href="#overview" aria-label="Windline, обзор"><span class="brand-symbol">✳</span><span>WINDLINE<span class="brand-period">.</span><small>FORECAST DESK · ALMATY</small></span></a> <p class="nav-caption">РАБОЧЕЕ ПРОСТРАНСТВО</p> <nav aria-label="Разделы"><a class="nav-link active" href="#overview" aria-current="page"><span class="nav-icon">◫</span> Обзор</a><a class="nav-link" href="#forecast"><span class="nav-icon">⌁</span> Прогноз</a><a class="nav-link" href="#backtest"><span class="nav-icon">▥</span> Бэктест</a><a class="nav-link" href="#provenance"><span class="nav-icon">◇</span> Источники данных</a></nav> <div class="side-bottom"><span class="site-dot"></span><div><strong>ВЭС Алматы</strong><small>2 турбины · Казахстан</small></div></div> ` })} <main id="overview" class="main-content"> <header class="topbar"><div class="breadcrumbs">Объекты <span>/</span> ВЭС Алматы <span>/</span> <strong>Обзор</strong></div><div class="top-right"><span class="demo-pill">ДЕМО ДАННЫЕ</span><span class="top-avatar" aria-label="Рабочее пространство Windline">W</span></div></header> <div class="content"> <section class="heading"><div><div class="eyebrow"><span class="eyebrow-line"></span> WIND POWER / OPERATIONS</div><h1>Обзор прогноза</h1><p>Мониторинг ожидаемой выработки ветропарка в Алматы.</p></div><div class="heading-state">${renderComponent($$result, "Badge", $$Badge, { "variant": "warning" }, { "default": ($$result2) => renderTemplate`● Требует проверки` })}<small>Запуск от 01 фев 2026, 00:00 UTC+5</small></div></section> ${renderComponent($$result, "Alert", $$Alert, { "variant": "warning", "class": "status-alert" }, { "default": ($$result2) => renderTemplate`<div class="alert-inner"><div class="alert-symbol">!</div><div><strong>Прогноз сформирован с ограничениями</strong><p>Проверьте модель и происхождение погодных данных, прежде чем использовать результаты для принятия решений.</p></div><a href="#provenance">Проверить данные <span aria-hidden="true">↗</span></a></div>` })} <section class="metrics" aria-label="Ключевые показатели">${renderComponent($$result, "Stat", $$Stat, { "label": "\u0413\u043E\u0440\u0438\u0437\u043E\u043D\u0442 \u043F\u0440\u043E\u0433\u043D\u043E\u0437\u0430", "value": "48 \u0447\u0430\u0441\u043E\u0432" })}${renderComponent($$result, "Stat", $$Stat, { "label": "\u0428\u0430\u0433 \u0440\u0430\u0441\u0447\u0451\u0442\u0430", "value": "1 \u0447\u0430\u0441" })}${renderComponent($$result, "Stat", $$Stat, { "label": "\u0411\u044D\u043A\u0442\u0435\u0441\u0442", "value": "\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445" })}${renderComponent($$result, "Stat", $$Stat, { "label": "\u041D\u0430\u0431\u043B\u044E\u0434\u0435\u043D\u0438\u0435", "value": "\u041D\u0435\u0442 \u0434\u0430\u043D\u043D\u044B\u0445" })}</section> <section class="workspace" aria-label="Прогноз и настройки"><div class="primary-column"> ${renderComponent($$result, "Card", $$Card, { "as": "section", "class": "forecast-card", "id": "forecast" }, { "default": ($$result2) => renderTemplate`<div class="section-heading"><div><div class="eyebrow">ПРОГНОЗ ВЫРАБОТКИ</div><h2>Почасовая мощность</h2><p>Нормализованная активная мощность, % от номинала</p></div>${renderComponent($$result2, "Badge", $$Badge, { "variant": "secondary" }, { "default": ($$result3) => renderTemplate`2 турбины` })}</div> ${renderComponent($$result2, "LineChart", $$LineChart, { "aria-label": "\u0418\u043B\u043B\u044E\u0441\u0442\u0440\u0430\u0442\u0438\u0432\u043D\u0430\u044F \u043F\u043E\u0447\u0430\u0441\u043E\u0432\u0430\u044F \u043C\u043E\u0449\u043D\u043E\u0441\u0442\u044C \u0434\u0432\u0443\u0445 \u0442\u0443\u0440\u0431\u0438\u043D", "series": series, "formatValue": ((v) => `${v}%`), "markers": "all", "showTable": true, "showLegend": true, "caption": "\u0418\u043B\u043B\u044E\u0441\u0442\u0440\u0430\u0442\u0438\u0432\u043D\u044B\u0435 \u0434\u0430\u043D\u043D\u044B\u0435 \u0438\u043D\u0442\u0435\u0440\u0444\u0435\u0439\u0441\u0430. \u0412 \u043F\u043E\u0434\u043A\u043B\u044E\u0447\u0451\u043D\u043D\u043E\u043C \u043F\u0440\u0438\u043B\u043E\u0436\u0435\u043D\u0438\u0438 \u0437\u0434\u0435\u0441\u044C \u043E\u0442\u043E\u0431\u0440\u0430\u0436\u0430\u044E\u0442\u0441\u044F \u0440\u0435\u0430\u043B\u044C\u043D\u044B\u0435 \u0440\u0435\u0437\u0443\u043B\u044C\u0442\u0430\u0442\u044B \u043C\u043E\u0434\u0435\u043B\u0438.", "class": "power-chart" })} <div class="chart-note"><span class="note-icon">i</span><span>Фактическая серия и доверительные интервалы должны поступать из API прогноза.</span></div> ` })} ${renderComponent($$result, "Card", $$Card, { "as": "section", "id": "backtest", "class": "backtest-card" }, { "default": ($$result2) => renderTemplate`<div class="section-heading"><div><div class="eyebrow">КАЧЕСТВО МОДЕЛИ</div><h2>Бэктест</h2></div>${renderComponent($$result2, "Badge", $$Badge, { "variant": "outline" }, { "default": ($$result3) => renderTemplate`Недоступен` })}</div><div class="empty-state"><div class="empty-icon">▥</div><div><strong>Пока нечего сравнивать</strong><p>Для февраля нет размеченных фактических данных по турбинам. Метрики появятся после загрузки наблюдений.</p></div></div>` })} </div><div class="secondary-column"> ${renderComponent($$result, "Card", $$Card, { "as": "section", "class": "form-card" }, { "default": ($$result2) => renderTemplate`<div class="eyebrow">НОВЫЙ РАСЧЁТ</div><h2>Создать прогноз</h2><p class="form-intro">Выберите точку отсчёта и длительность.</p><form id="forecast-form">${renderComponent($$result2, "Field", $$Field, {}, { "default": ($$result3) => renderTemplate`${renderComponent($$result3, "Label", $$Label, { "for": "origin" }, { "default": ($$result4) => renderTemplate`Начало прогноза` })}${renderComponent($$result3, "Input", $$Input, { "id": "origin", "name": "origin", "type": "datetime-local", "value": "2026-02-01T00:00", "required": true })}` })}${renderComponent($$result2, "Field", $$Field, {}, { "default": ($$result3) => renderTemplate`${renderComponent($$result3, "Label", $$Label, { "for": "horizon" }, { "default": ($$result4) => renderTemplate`Горизонт` })}${renderComponent($$result3, "NativeSelect", $$NativeSelect, { "id": "horizon", "name": "horizon", "options": [{ label: "24 \u0447\u0430\u0441\u0430", value: "24" }, { label: "48 \u0447\u0430\u0441\u043E\u0432", value: "48" }, { label: "72 \u0447\u0430\u0441\u0430", value: "72" }], "value": "48" })}` })}${renderComponent($$result2, "Button", $$Button, { "type": "submit", "class": "full-button" }, { "default": ($$result3) => renderTemplate`Запустить прогноз <span aria-hidden="true">↗</span>` })}</form><div class="form-divider"></div>${renderComponent($$result2, "Button", $$Button, { "id": "refresh-weather", "variant": "outline", "class": "full-button" }, { "default": ($$result3) => renderTemplate`Обновить погодные данные` })}<p id="action-feedback" class="feedback" role="status" aria-live="polite"></p>` })} ${renderComponent($$result, "Card", $$Card, { "as": "section", "id": "provenance", "class": "source-card" }, { "default": ($$result2) => renderTemplate`<div class="section-heading"><div><div class="eyebrow">ПРОИСХОЖДЕНИЕ ДАННЫХ</div><h2>Погода и объект</h2></div></div><div class="source-row"><span>Погодная модель</span><strong>hindcast</strong></div><div class="source-row"><span>Проверка по времени</span>${renderComponent($$result2, "Badge", $$Badge, { "variant": "warning" }, { "default": ($$result3) => renderTemplate`Не подтверждена` })}</div><div class="source-row"><span>Расположение</span><strong>Алматы, KZ</strong></div><div class="turbines"><div class="turbine"><span class="turbine-dot green"></span><div><strong>Турбина 01</strong><small>43.645150° N · 78.535604° E</small></div></div><div class="turbine"><span class="turbine-dot blue"></span><div><strong>Турбина 02</strong><small>43.643198° N · 78.538828° E</small></div></div></div>` })} </div></section><footer>WINDLINE / FORECAST DESK <span>Демонстрационный интерфейс · данные и действия не подключены к сервису</span></footer> </div> </main> </div> ${renderScript($$result, "/workspace/scratch/1bccac39c61e/windline-demo/src/pages/index.astro?astro&type=script&index=0&lang.ts")} </body> </html>`;
}, "/workspace/scratch/1bccac39c61e/windline-demo/src/pages/index.astro", void 0);

const $$file = "/workspace/scratch/1bccac39c61e/windline-demo/src/pages/index.astro";
const $$url = "";

const _page = /*#__PURE__*/Object.freeze(/*#__PURE__*/Object.defineProperty({
  __proto__: null,
  default: $$Index,
  file: $$file,
  url: $$url
}, Symbol.toStringTag, { value: 'Module' }));

const page = () => _page;

export { page };
