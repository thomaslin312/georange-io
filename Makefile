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
CHUNK_CONFIGS ?= TUNED_chunk16k TUNED_chunk256k TUNED_chunk1m
RTT_CONFIGS   ?= DEFAULT TUNED_chunk16k TUNED_chunk16k_mt
CONFIGS      ?= DEFAULT TUNED_chunk16k TUNED_chunk256k TUNED_chunk1m
WORKLOADS    ?= w1 w2 w3 w4 w5
MAX_WALL_S   ?= 1800

.PHONY: baseline up down clean-results stage index specs sweep \
        sweep-chunks sweep-rtt sweep-bwcap sweep-full check-rtt-invariance \
        oracle granularity \
        analyze manifest report status logs verify crosscheck

baseline: up stage index specs sweep oracle granularity prefix checkpoint crosscheck analyze \
          check-rtt-invariance manifest report
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
#
# Run in two phases. What GDAL fetches is decided by its own configuration and
# by the file layout, not by how long the network takes to answer, so the
# chunk-size sweep is run at one RTT and only the two configurations that
# matter for the latency story are carried across the full RTT range. This
# costs 40 measurements instead of 60 and spends far less of the budget on the
# slow 150 ms cells. The RTT-independence of the byte and request counts is
# checked, not assumed: `make check-rtt-invariance` verifies it against the
# results that come out.
sweep: sweep-chunks sweep-rtt sweep-bwcap

sweep-chunks:
	$(PY) baseline/sweep.py --workloads $(WORKLOADS) \
	    --configs $(CHUNK_CONFIGS) --rtts $(HEADLINE_RTT) \
	    --max-wall-s $(MAX_WALL_S) --skip-existing

sweep-rtt:
	$(PY) baseline/sweep.py --workloads $(WORKLOADS) \
	    --configs $(RTT_CONFIGS) --rtts $(RTTS) \
	    --max-wall-s $(MAX_WALL_S) --skip-existing

# The testbed has effectively unlimited bandwidth, so over-fetched bytes are
# nearly free and the chunk-size trade looks like a wash on wall time. This
# repeats part of the matrix with the response throughput capped, which is what
# separates the cost of extra round trips from the cost of extra bytes.
BWCAP_MBPS    ?= 100
BWCAP_WORKLOADS ?= w2 w3

sweep-bwcap:
	$(PY) baseline/sweep.py --workloads $(BWCAP_WORKLOADS) \
	    --configs $(CHUNK_CONFIGS) --rtts $(HEADLINE_RTT) \
	    --bandwidth-mbps $(BWCAP_MBPS) --out-dir results/runs_bwcap \
	    --max-wall-s $(MAX_WALL_S) --skip-existing

sweep-full:
	$(PY) baseline/sweep.py --workloads $(WORKLOADS) --configs $(CONFIGS) \
	    --rtts $(RTTS) --max-wall-s $(MAX_WALL_S) --skip-existing

check-rtt-invariance:
	$(PY) baseline/check_invariance.py

oracle:
	$(BENCH) python3 baseline/oracle_w5.py

checkpoint:
	$(BENCH) python3 baseline/gate_checkpoint.py --blocks 300

prefix:
	$(BENCH) python3 baseline/prefix_decode.py

granularity:
	$(BENCH) python3 baseline/granularity.py

crosscheck:
	$(PY) baseline/crosscheck.py

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
	rm -rf results/runs results/runs_bwcap results/raw/*.jsonl \
	       results/raw/*.jsonl.gz \
	       results/plots results/summary.csv results/tables.md \
	       results/tables.json results/diagnosis.json results/granularity.json \
	       results/rtt_invariance.json results/w5_oracle.json
