import tempfile
import unittest
from pathlib import Path

from scripts.db_init import create_database
from scripts.goals import deactivate_goal, list_goals, save_goal


class GoalTests(unittest.TestCase):
    def test_goal_can_be_saved_updated_and_deactivated(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            save_goal(
                "Emergency fund",
                "10000",
                "2500.50",
                "NZD",
                "2027-06-01",
                database_path,
            )
            save_goal(
                "Emergency fund",
                "12000",
                "3000",
                "NZD",
                "2027-12-01",
                database_path,
            )
            goals = list_goals(database_path=database_path)

            self.assertEqual(len(goals), 1)
            self.assertEqual(goals[0][1:6], ("Emergency fund", "NZD", 1200000, 300000, "2027-12-01"))

            deactivate_goal("Emergency fund", database_path)
            self.assertEqual(list_goals(database_path=database_path), [])


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
