# Lumière Resident: product sheet

`lumiere-resident.html` is the product design sheet (system, panels, inside
layout, wiring schematic, parts, finish, assembly). Open it in a browser.

The panel drawings, the inside layout and the schematic come from
`drawings.py`, which reads `../enclosure/enclosure.py`: when a dimension changes
there, run `python3 drawings.py > drawings.json` and rebuild the page so the sheet
always matches the printed part.
