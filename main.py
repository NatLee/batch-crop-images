"""The original entry point: ``python main.py --input_path DIR --output_path DIR`` from a clone.

Kept so the commands in old tutorials, scripts and the 2019 README keep working. It runs exactly the
same code as the installed ``batch-crop`` command. It only adds ``src/`` to the import path so that a
plain clone works after ``pip install -r requirements.txt``; it can go once nobody runs the tool
from a clone without installing it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from batch_crop.cli import main  # needs the path above

if __name__ == "__main__":
    raise SystemExit(main())
