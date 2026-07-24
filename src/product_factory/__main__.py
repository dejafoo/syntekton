"""Allow `python -m product_factory` (used by host worker spawn)."""

from product_factory.cli.app import main

if __name__ == "__main__":
    main()
