# GUI smoke test: drives the real crop window on a virtual screen.
# Xvfb provides the screen, xdotool the mouse and keyboard. From the repository root:
#
#   docker build -f tests/gui/Dockerfile -t batch-crop-gui .
#   docker run --rm batch-crop-gui
FROM python:3.12-slim

# Xvfb + xdotool, and the system libraries OpenCV's bundled Qt window needs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb xauth xdotool libgl1 libglib2.0-0 libsm6 libxext6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --root-user-action=ignore ".[test]"
COPY tests ./tests

ENV BATCH_CROP_GUI_TESTS=1 PYTHONDONTWRITEBYTECODE=1
# xvfb-run must not be process 1: it waits for a signal from Xvfb that process 1 never receives.
CMD ["sh", "-c", "xvfb-run --auto-servernum --server-args='-screen 0 1600x1000x24' python -m pytest -m gui -v -p no:cacheprovider"]
