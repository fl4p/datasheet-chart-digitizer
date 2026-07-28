---
name: dsdig-annotated-pdf-reproducibility
description: Hash-locked PDFs with embedded overlays must be saved with PyMuPDF no_new_id=True; otherwise identical overlays produce different trailer IDs and packet hashes
metadata:
  type: project
---

Discovered while building the random-PDF SPD03N50C3ATMA1-HXY review artifact on
2026-07-19. Reopening the same source PDF, inserting the same five overlay PNGs, and
calling `Document.save(..., garbage=4, deflate=True)` produced different PDF SHA-256
values on consecutive runs because PyMuPDF generated a new trailer document ID.

For a byte-reproducible, hash-locked review packet use
`Document.save(..., garbage=4, deflate=True, no_new_id=True)`, then build twice and
assert the PDF hashes match. The source PDF, panel crop boxes, overlay images, result
JSONs, and embedded PDF must all be content-hashed. A stable rendered page image is not
enough: the actual PDF handed to Fab is the reviewed artifact.

