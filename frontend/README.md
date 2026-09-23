# Windline frontend

The product dashboard is an Astro app built with Lumen UI. It reads and runs forecasts through the local Python API; it contains no sample forecast data.

From the repository root, start the API in one terminal and the frontend in another:

```sh
python -m wind_forecast.presentation.api --host 127.0.0.1 --port 8000
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:4321>. The Astro development server proxies `/api` to `127.0.0.1:8000`. The API mode is server-configured; the browser does not choose demo or competition mode.

Run the frontend checks from this directory:

```sh
npm test
npm run check
npm run build
```
