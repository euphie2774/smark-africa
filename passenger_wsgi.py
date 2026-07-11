import os
import sys


PROJECT_HOME = os.path.dirname(os.path.abspath(__file__))

if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

from main import app as application
