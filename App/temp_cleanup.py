import os
import shutil
import glob


def clean_temp(base_dir: str):
    """
    Clean temporary files and folders created during processing.
    """
    patterns = [
        os.path.join(base_dir, '**', 'temp_BG_REMOVED'),
        os.path.join(base_dir, '**', '*_compressed.jpg'),
        os.path.join(base_dir, '**', '*_converted.png'),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.isfile(path):
                    os.remove(path)
            except Exception as e:
                print(f"Warning: Could not remove {path}: {e}")
