"""A real electric moka pot run (15 Nov 2026), read off the history graph.

Seconds after 13:05:00 and watts; the coffee was ready at MARK_DONE, the pot switched
itself off at MARK_OFF. Values are approximate (read from a screenshot).
"""

SAMPLES = [
    (25, 172), (35, 265), (47, 230), (55, 222), (70, 218), (90, 214), (120, 212), (150, 209), (180, 205),
    (210, 202), (240, 199), (270, 196), (300, 192), (330, 189), (360, 186), (390, 183), (420, 180),
    (450, 178), (470, 175), (480, 173), (530, 166), (540, 158), (555, 152), (575, 147), (600, 141),
    (630, 140), (650, 136), (675, 132), (700, 128), (730, 124), (760, 121), (800, 118), (840, 115),
    (880, 112), (920, 110), (950, 109), (985, 108), (995, 0),
]
START = 25
MARK_DONE = 525
MARK_OFF = 995
