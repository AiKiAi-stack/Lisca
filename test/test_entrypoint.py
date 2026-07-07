# encoding:utf-8
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
from streamparser.entrypoint import main
from streamparser.logger import Loggers

if __name__ == "__main__":
    Loggers(verbose=True)
    sys.argv.extend(
        [
            ".env/test_config.yml",
            "-d",
        ]
    )
    sys.exit(main())
