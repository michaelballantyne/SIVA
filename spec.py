# Inspect -> load (strided for the browser) -> render the heptane volume.
info   = inspect("/vast/projects/autonomousvis/ashrestha/code/VisLang/csafe_heptane_302x302x302_uint8.raw")
loaded = load(info, dimensions={'grid': 150})   # 302^3 -> ~151^3 for browser responsiveness
render(loaded, cmap='inferno')   # dark hellfire ramp — black -> blood red -> orange
