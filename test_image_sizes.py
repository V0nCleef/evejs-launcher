from PyQt6.QtGui import QPixmap
from pathlib import Path
for p in sorted(Path("assets/hero").glob("hero_*.png")):
	pm=QPixmap(str(p))
	print(f"{p.name}: {pm.width()}x{pm.height()}")
