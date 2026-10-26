# EVision implementation traceability

This document tracks the current implementation against the active product
requirements and the retained original specification.

| Area | Current implementation | Evidence | Remaining external setup |
|---|---|---|---|
| Synthetic data | Deterministic four-city replay store | `backend/app/replay.py` | None |
| Queueing | Stable Erlang-C with saturation and wait cap | `backend/app/decision.py` | None |
| Recommendations | Candidate generation, delays, Pareto front, three labels | `backend/app/decision.py`, `/api/v1/recommend` | None |
| Routing | OSRM-compatible provider with haversine fallback | `backend/app/routing.py`, `/api/v1/route` | Optional OSRM URL |
| Map | Leaflet/OpenStreetMap tiles, origin, radius, markers, route preview | `frontend/src/live-dashboard.tsx` | Internet or tile mirror |
| AI model | Optional XGBoost horizon models with deterministic fallback | `backend/app/forecasting.py`, `backend/app/model_registry.py` | Install xgboost for XGBoost training |
| Replay evaluation | Deterministic synthetic replay artifact generation | `backend/app/evaluation.py`, `backend/scripts/run_evaluation.py` | None |
| Authentication | Supabase JWT verification and frontend login flow | `backend/app/auth.py`, `frontend/src/auth-page.tsx` | Supabase project settings |
| User features | Profiles, preferences, favourites, recommendation history | `backend/app/models.py`, user API routes | PostGIS migration + Supabase |
| Real datasets | Existing OCM importer and database catalogue path | `backend/scripts/import_ocm.py`, `load_catalogue.py` | Reviewed source files |
| Evaluation reports | API exposes generated replay metrics; forecast ablations still pipeline work | `backend/app/main.py` | Run model/evaluation extensions |

Synthetic occupancy, forecasts, queue values, and scenario carbon are not live
observations. The UI labels those states explicitly.
