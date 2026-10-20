"""``python -m dq.extract`` extrai a origem; ``python -m dq.extract snapshot`` refaz a amostra."""

import sys

from dq.extract.snapshot import build_sample
from dq.extract.source import extract
from dq.settings import get_settings

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "snapshot":
        for name, n in build_sample().items():
            print(f"  amostra {name:<22} {n:>8,}")
    else:
        s = get_settings()
        print(f"Extraindo origem ({s.source['kind']}, perfil {s.profile}) para {s.path('source')}")
        extract(s)
