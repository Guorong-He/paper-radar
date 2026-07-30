import unittest

from paper_radar.venues import matches_preferred_venue


class VenueTests(unittest.TestCase):
    def test_flagship_venues_match_exactly(self):
        preferred = ["Science", "Nature", "Cell", "Science Robotics"]

        self.assertTrue(matches_preferred_venue("Science", preferred))
        self.assertTrue(matches_preferred_venue("Science Robotics", preferred))
        self.assertFalse(matches_preferred_venue("IEEE Transactions on Automation Science and Engineering", preferred))
        self.assertFalse(matches_preferred_venue("Cell Reports Physical Science", preferred))

    def test_short_conference_acronyms_match_whole_tokens(self):
        preferred = ["CHI", "ICRA", "RSS", "CoRL"]

        self.assertTrue(matches_preferred_venue("Proceedings of CHI", preferred))
        self.assertFalse(matches_preferred_venue("Lab on a Chip", preferred))
        self.assertFalse(matches_preferred_venue("Press and Society", preferred))


if __name__ == "__main__":
    unittest.main()
