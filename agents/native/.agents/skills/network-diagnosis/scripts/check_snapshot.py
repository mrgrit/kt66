#!/usr/bin/env python3
"""저장된 network-*.json의 판정 재현: python check_snapshot.py <파일>."""
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    root = next(p for p in Path(__file__).resolve().parents if (p / 'network_probe.py').is_file())
    sys.path.insert(0, str(root))
    from network_probe import assess
    evidence = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps(assess(evidence['observations'], evidence['baseline']), ensure_ascii=False, indent=2))
