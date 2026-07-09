# SIVA raw-Docker deployment image.
#
# Runs SIVA *and* the Claude Code CLI fully inside the container, as an
# isolation boundary: the container gets no host credentials it isn't
# explicitly given, data is mounted read-only, and only the trame port is
# published (to host loopback). Rendering is software (Mesa/OSMesa, no GPU) —
# see deploy/docker/README.md for the GPU story and the perf caveats.
#
# Build with the repo root as context (build.sh does this):
#   docker build -f deploy/docker/Dockerfile -t siva:latest .
FROM python:3.11-slim

# System deps:
#  - Mesa/libGL + libOSMesa: software OpenGL for headless VTK (no GPU / no X).
#  - libx*/libsm/libglib: VTK runtime shared-lib deps.
#  - node + Claude Code CLI: the in-container driver for the SIVA MCP server.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglx-mesa0 libgl1-mesa-dri libosmesa6 \
        libxrender1 libxext6 libsm6 libxt6 libx11-6 libglib2.0-0 \
        curl gnupg ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# Install SIVA (+ the trame extra, for browser views) into a container venv.
WORKDIR /opt/siva
COPY pyproject.toml ./
COPY siva ./siva
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir ".[trame]"
ENV PATH=/opt/venv/bin:$PATH

# Headless software rendering via OSMesa: no X server / Xvfb needed. SIVA
# builds render windows through the vtkRenderWindow factory, which honors this.
ENV VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow

# Keep Claude Code's config/auth on the mounted volume (see run.sh), not the
# container's ephemeral home — so login/theme/onboarding survive recreation.
ENV CLAUDE_CONFIG_DIR=/root/.claude

# Bind-mounted at runtime: view-*.py, data (read-only), the .siva scratch dir.
WORKDIR /work
CMD ["sleep", "infinity"]
