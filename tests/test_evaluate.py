import unittest

from scripts.evaluate import Company, canonical, language, scores


class EvaluationTest(unittest.TestCase):
    def test_template_language_and_repeated_text(self):
        self.assertEqual(canonical(" HOTEL butik di Ipoh  Hotel butik di Ipoh "), "hotel butik di ipoh")
        self.assertEqual(language("Hotel butik di Ipoh"), "Malay")
        self.assertEqual(language("Boutique hotel in Ipoh"), "English")
        with self.assertRaises(ValueError):
            language("Unreviewed template")

    def test_filter_before_ranking_and_keep_unlabelled_candidates(self):
        texts = ["Boutique hotel", "Boutique hotel Boutique hotel", "Hotel butik", "Kebun durian"]
        pool = [Company("A", texts[0], "Hospitality")]
        pool += [Company(f"B{i:02}", texts[1], None) for i in range(12)]
        pool += [Company(f"C{i:02}", texts[2], "Hospitality") for i in range(12)]
        pool += [Company(f"D{i:02}", texts[3], "Agriculture") for i in range(12)]
        cosine = [[1, .99, .9, .1], [.99, 1, .9, .1], [.9, .9, 1, .1], [.1, .1, .1, 1]]
        result = scores(pool, texts, cosine)
        # The only English query ranks unlabelled neighbours ahead of the Malay hotels.
        self.assertEqual(result["English", "All"], (1, 0, 0))
        self.assertEqual(result["English", "Different text"], (1, 1, 1))
        self.assertEqual(result["English", "Other language"], (1, 1, 1))
        self.assertEqual(result["Malay", "All"][0], 24)
