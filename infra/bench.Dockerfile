# Pinned GDAL. Upstream maintains only the latest release branch, so the
# baseline is measured against current stable and the exact versions of
# GDAL / PROJ / libcurl are recorded in every results row.
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.13.3

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3-pip python3-dev build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# rasterio built against the image's GDAL, never a wheel carrying its own copy.
RUN python3 -m pip install --no-cache-dir --no-binary rasterio \
      "rasterio==1.5.1" \
 && python3 -m pip install --no-cache-dir \
      "numpy>=1.26,<3" "tifffile>=2024.1.30" "boto3>=1.34" "aiohttp>=3.9" \
      "matplotlib>=3.8" "PyYAML>=6" "psutil>=5.9" "scikit-image>=0.22" \
      "pytest>=8" \
      "shapely>=2.0" "pandas>=2.1" "heapdict>=1.0.1"

WORKDIR /work
