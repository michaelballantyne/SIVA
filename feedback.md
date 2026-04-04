  Data exploration gaps:

  1. Line probes / profiles with plots. I could sample a single point, but I repeatedly wanted to extract a 1D profile - e.g., temperature vs. height
  through the plume center, or wind speed along a horizontal transect. A probe_line(start, end, field) -> plot tool would have been the most useful
  addition for interpretation. Right now I can describe that "theta reaches 1184K" but I can't show where in the vertical column that peak is or how
  sharp the gradient is.
  2. Conditional / subregion statistics. I could get global stats, but I wanted things like "mean updraft velocity where theta > 400K" or "total
  volume where O2 < 0.20." Being able to query statistics on a filtered subset without building a full pipeline would help with quantitative
  interpretation.
  3. Multi-timestep awareness. The filename output.30000.vts implies a time series. I had no way to discover sibling timesteps, load a sequence, or
  show how the fire evolved. Even just listing related files by pattern would help.

  Presentation gaps:

  4. Text annotations on the scene. I couldn't label "fire head," "ridge," "burn scar" directly on the image. I had to explain everything in separate
  text, which disconnects the explanation from the visual. A annotate(position, label) or even annotate_2d(x, y, text) would make screenshots
  self-explanatory.
  5. Multi-panel / side-by-side layouts. I had to show fields one at a time. A 2x2 grid comparing theta, O2, w, and fuel on the same cross-section
  would have been far more effective for a human than four sequential screenshots.
  6. 2D chart rendering. Histograms and statistics come back as text. An actual rendered line plot or histogram image (e.g., from the profile probe
  above) would communicate distributions and gradients much more effectively than numbers.
  7. Camera orbit / turntable. A short animated rotation around the fire plume would help a human grasp the 3D structure far better than any single
  viewpoint I can choose. Even 4-6 frames from different angles returned as a strip would help.

  If I had to pick just two, it would be line probes with plots and scene annotations - those are where I felt the biggest gap between what I
  understood about the data and what I could actually communicate visually.