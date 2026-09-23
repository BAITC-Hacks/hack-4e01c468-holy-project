# Windline — Lumen UI demo

Responsive Astro dashboard for a wind farm forecast desk, using `@santi020k/lumen-astro` components. The plotted values are illustrative; forecast and weather buttons display demo feedback and do not call a backend.

## Run

```bash
npm install
npm run dev
```

Open the URL printed by Astro. To build static files, run `npm run build` and use the `dist/` folder. The UI is in `src/pages/index.astro` and `src/styles/dashboard.css`. Replace the `series` array with API data and wire the form and weather button to your own endpoints.
