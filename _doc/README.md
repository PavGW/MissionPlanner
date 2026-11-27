# MissionPlanner – documentation

| File | Content |
|---|---|
| [01_requirements.md](01_requirements.md) | Interview, decisions, viability assessment |
| [02_design.md](02_design.md) | Architecture, data model, camera model, algorithms |
| [03_dev_log.md](03_dev_log.md) | Development log: thinking, steps, results |
| [04_github_survey.md](04_github_survey.md) | Open-source projects targeting the DJI Neo; cross-check of the telemetry map |
| [05_acquisition_workflow.md](05_acquisition_workflow.md) | **Field workflow**: plan → prepare → fly → field QA → process → stitch → deliver |
| [06_simulator_guide.md](06_simulator_guide.md) ([PDF](06_simulator_guide.pdf)) | Self-built drone project: ArduPilot simulator (SITL) setup and five first exercises |
| `data/` | Small result tables (inventories, test results) |

## Running

```
run_app.bat                      # creates .venv on first run, opens http://127.0.0.1:8080
docker run -d --name nodeodm -p 3000:3000 --restart unless-stopped opendronemap/nodeodm   # once, for stitching
```

Projects are stored in `projects/<name>/` (`project.json`, `<name>.gpkg`, `flights/`, `frames/`, `exports/`, `odm/`).

