# CT Head

Stanford CT Head dataset — 113-slice CT scan of a human head.

- **Source:** https://graphics.stanford.edu/data/voldata/
- **Format:** vtkXMLImageData (`.vti`), 256x256x113, uint16, zlib-compressed, spacing 1×1×2 mm
- **Download:** `bash download.sh`
- **DSL usage:** `data = load("data/cthead.vti")`
