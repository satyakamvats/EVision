# EVision

EVision is a decision-support web application for electric-vehicle charging. It forecasts short-term station occupancy, estimates queueing time, and presents transparent fastest, balanced, and lowest-carbon charging choices.

The repository is split into product documentation, frontend implementation, and developer utilities. The current deliverable is the React frontend with typed mock data and an API-ready boundary.

## Repository map

```text
Evision/
├── frontend/                 React + TypeScript + Vite application
│   ├── public/                Static media and public assets
│   ├── src/                   Routes, UI components, styles, and mock data
│   ├── package.json           Frontend scripts and dependencies
│   └── vite.config.ts         Vite configuration
├── docs/                     Product, architecture, delivery, and quality docs
│   ├── product/               Product requirements and scope
│   ├── architecture/          System design and boundaries
│   ├── delivery/              Implementation plan and release workflow
│   ├── quality/               QA and acceptance guidance
│   └── reference/             Original source documents retained for traceability
├── scripts/                  Small developer and media utilities
│   └── media/                Animation/media preparation scripts
├── .gitignore
└── README.md
```

## Start the frontend

```powershell
cd frontend
npm install
npm run dev
```

Create a production build with `npm run build`.

## Run the four-city API

The FastAPI backend serves deterministic synthetic replay data for Bengaluru,
Mysuru, Hubballi–Dharwad, and Belagavi. It uses local deterministic data by
default, so an Open Charge Map key is not needed to try the API.

```powershell
cd backend
Copy-Item .env.example .env
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Set `VITE_API_BASE_URL=http://localhost:8000/api/v1` in `frontend/.env.local`
to connect the Vite frontend. Without a running API, the frontend safely uses
its existing local demo data.

For a real station-location snapshot, set `EVISION_OCM_API_KEY` and run
`python -m scripts.import_ocm`. After reviewing the snapshot, initialise the
database and load it with:

```powershell
alembic upgrade head
python -m scripts.load_catalogue data/raw/ocm-karnataka.json
python -m scripts.generate_replay --months 6
```

Imported locations require provider attribution and must be reviewed before use;
operational values remain deterministic synthetic replay data.

Optional provider credentials belong only in the ignored `backend/.env` file:

```env
EVISION_OCM_API_KEY=replace-with-your-open-charge-map-key
EVISION_ELECTRICITY_MAPS_API_KEY=replace-with-your-electricity-maps-key
```

Open Charge Map supplies real location metadata. Electricity Maps is optional;
without it, EVision labels its carbon value as a scenario estimate.

## Documentation

- [Documentation index](docs/README.md)
- [Product requirements](docs/product/requirements.md)
- [System architecture](docs/architecture/system-architecture.md)
- [Delivery plan](docs/delivery/implementation-plan.md)
- [Quality and acceptance](docs/quality/quality-guide.md)

## Product routes

| Route | Purpose |
|---|---|
| `/` | EVision landing page |
| `/dashboard` | Charging recommendation dashboard |
| `/station/:id` | Station detail and forecast |
| `/compare` | Strategy comparison |
| `/evaluation` | Evaluation metrics |
| `/insights` | Network utilisation insights |
| `/about` | Methodology and assumptions |

## Project status

The frontend currently uses realistic local mock data. Backend integration is designed to fit behind the documented `/api/v1` service boundary. Synthetic or assumed values are labelled in the interface and must not be presented as live availability.
