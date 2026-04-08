from __future__ import annotations

from .config import SwindleConfig
from .usi_protocol import USIProtocol


def main() -> None:
    protocol = USIProtocol(SwindleConfig())
    protocol.run()


if __name__ == "__main__":
    main()
