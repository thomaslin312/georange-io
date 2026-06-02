# GeoRange IO Phase 0 -- baseline instrumentation and headroom analysis.
#
#   make baseline    the whole thing: infra up, stage, index, specs, sweep,
#                    oracle, analysis, MANIFEST.md and REPORT.md tables
#
# Individual targets are safe to re-run; staging and indexing are idempotent.

COMPOSE := docker compose -f infra/docker-compose.yml
BENCH   := $(COMPOSE) exec -T bench
PY      := python3

HEADLINE_RTT ?= 50
RTTS         ?= 5 50 150
CONFIGS      ?= DEFAULT TUNED_chunk16k TUNED_chunk256k TUNED_chunk1m
WORKLOADS    ?= w1 w2 w3 w4 w5
MAX_WALL_S   ?= 900

.PHONY: baseline up down clean-results stage index specs sweep oracle \
        analyze manifest report status logs verify

baseline: up stage index specs sweep oracle analyze manifest report
	@echo
	@echo "Phase 0 complete. See REPORT.md, results/summary.csv, results/plots/."

## --- infrastructure -------------------------------------------------------
up:
	$(COMPOSE) up -d --build
	@echo "waiting for the proxy control API ..."
	@for i in $$(seq 1 60); do \
	  curl -sf http://127.0.0.1:9210/health >/dev/null && break || sleep 1; done
	@curl -s http://127.0.0.1:9210/health && echo

down:
	$(COMPOSE) down

status:
	$(COMPOSE) ps
	@curl -s http://127.0.0.1:9210/health && echo

logs:
	$(COMPOSE) logs --tail 60 proxy minio

## --- corpus ---------------------------------------------------------------
sources:
	$(PY) data/resolve_sources.py

stage:
	$(PY) data/fetch.py --jobs 3

index:
	$(BENCH) python3 baseline/cog_index.py

## --- workloads ------------------------------------------------------------
specs:
	$(BENCH) python3 baseline/generate_all.py

## --- measurement ----------------------------------------------------------
sweep:
	$(PY) baseline/sweep.py --workloads $(WORKLOADS) --configs $(CONFIGS) \
	    --rtts $(RTTS) --max-wall-s $(MAX_WALL_S)

oracle:
	$(BENCH) python3 baseline/oracle_w5.py

## --- analysis -------------------------------------------------------------
analyze:
	$(BENCH) python3 baseline/diagnose.py
	$(BENCH) python3 baseline/analyze.py --headline-rtt $(HEADLINE_RTT)

manifest:
	$(PY) data/make_manifest.py

report:
	$(PY) baseline/render_report.py

verify:
	$(BENCH) python3 -c "from osgeo import gdal; import rasterio; \
	print('GDAL', gdal.VersionInfo('RELEASE_NAME')); \
	print('rasterio', rasterio.__version__, 'PROJ', rasterio.__proj_version__)"

clean-results:
	rm -rf results/runs results/raw/*.jsonl results/raw/*.jsonl.gz \
	       results/plots results/summary.csv results/tables.md \
	       results/tables.json results/diagnosis.json
