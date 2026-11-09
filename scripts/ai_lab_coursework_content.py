"""Original realistic Ukrainian Informatics coursework content for AI-Lab.

Aggregates 35 authentic lessons (5 lessons x 7 classes from 5-A to 11-A)
with 175 realistic student submissions reflecting Ukrainian curriculum standards
(NUSH for grades 5-9, general/traditional for grades 10-11).
"""
import sys
from pathlib import Path

# Add scripts directory to sys.path so coursework_data can be imported cleanly
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from coursework_data.grade5 import LESSONS_5
from coursework_data.grade6 import LESSONS_6
from coursework_data.grade7 import LESSONS_7
from coursework_data.grade8 import LESSONS_8
from coursework_data.grade9 import LESSONS_9
from coursework_data.grade10 import LESSONS_10
from coursework_data.grade11 import LESSONS_11

LESSONS = (
    LESSONS_5 +
    LESSONS_6 +
    LESSONS_7 +
    LESSONS_8 +
    LESSONS_9 +
    LESSONS_10 +
    LESSONS_11
)

assert len(LESSONS) == 35, f"Expected 35 lessons, got {len(LESSONS)}"
assert {l['grade'] for l in LESSONS} == set(range(5, 12)), "Grades 5-11 must all be present"
