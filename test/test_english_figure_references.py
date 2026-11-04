import unittest

from exercise_extractor import extract_exercise_from_markdown
from exercise_extractor.extraction import extract_exercises


class EnglishFigureReferenceTests(unittest.TestCase):
    def test_public_markdown_api_attaches_figure_to_referencing_question(self):
        markdown = (
            '# Exercises\n\n'
            '1. Earlier question.\n\n'
            'FIGURE 14\n\n'
            '<img src="figure-14.jpg" />\n\n'
            '2. Use Figure 14 to prove the result.\n'
        )
        sections = list(extract_exercise_from_markdown(markdown, title='Exercises'))
        exercises = sections[0]['exercises']
        self.assertEqual(len(exercises), 2)
        self.assertNotIn('figure-14.jpg', exercises[0]['content'])
        self.assertIn('figure-14.jpg', exercises[1]['content'])

    def test_caption_before_image_attaches_to_later_reference(self):
        markdown = (
            '9. Previous question.\n\n'
            'FIGURE 14\n\n'
            '<div><img src="figure-14.jpg" /></div>\n\n'
            '10. Another question.\n\n'
            '11. Prove a result.\n\n'
            '12. Use Figure 14 (solid line) to prove the claim.\n'
        )
        node = {
            'title': 'Exercises',
            'has_exercise': True,
            'exercise_page_range': [1, 2],
            'exercise_content': markdown,
            'children': [],
        }
        extract_exercises([node])
        exercises = node['exercises']
        self.assertEqual(len(exercises), 4)
        self.assertIn('figure-14.jpg', exercises[3]['content'])
        for item in exercises[:3]:
            self.assertNotIn('figure-14.jpg', item['content'])

    def test_caption_after_image_supports_figure_number_and_fig_abbreviation(self):
        markdown = (
            '1. See Fig. 2.3 for the curve.\n\n'
            '2. Other question.\n\n'
            '<img src="curve.jpg" />\n'
            'Figure 2.3\n'
        )
        node = {
            'title': 'Exercises',
            'has_exercise': True,
            'exercise_page_range': [1, 2],
            'exercise_content': markdown,
            'children': [],
        }
        extract_exercises([node])
        by_number = {item['number']: item['content'] for item in node['exercises']}
        self.assertIn('curve.jpg', by_number['1'])
        self.assertNotIn('curve.jpg', by_number['2'])


if __name__ == '__main__':
    unittest.main()
