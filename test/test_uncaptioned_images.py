import unittest

from exercise_extractor import extract_exercise_from_markdown
from exercise_extractor.extraction import extract_exercises, extract_figures


class UncaptionedImageTests(unittest.TestCase):
    def test_public_section_preserves_uncaptioned_images_by_question(self):
        sections = list(extract_exercise_from_markdown(
            '# Exercises\n\n'
            '1. Read graph A.\n\n'
            '<div><img src="graph-a.jpg" /></div>\n\n'
            '2. Read graph B.\n\n'
            '<div><img src="graph-b.jpg" /></div>\n',
            title='Exercises',
        ))
        self.assertEqual(len(sections), 1)
        exercises = sections[0]['exercises']
        self.assertEqual(len(exercises), 2)
        self.assertIn('graph-a.jpg', exercises[0]['content'])
        self.assertNotIn('graph-b.jpg', exercises[0]['content'])
        self.assertIn('graph-b.jpg', exercises[1]['content'])
        self.assertIn('graph-a.jpg', sections[0]['content']['original'])
        self.assertIn('graph-b.jpg', sections[0]['content']['original'])

    def test_uncaptioned_images_stay_in_their_original_question(self):
        markdown = (
            '1. Read the graph.\n\n'
            '<div><img src="graph-1.jpg" /></div>\n\n'
            '2. Read the next graph.\n\n'
            '<div><img src="graph-2.jpg" /></div>\n'
        )
        cleaned, figures = extract_figures(markdown)
        self.assertEqual(figures, {})
        self.assertIn('graph-1.jpg', cleaned)
        self.assertIn('graph-2.jpg', cleaned)

        node = {'title': '1.1 Exercises', 'has_exercise': True,
                'exercise_page_range': [1, 2], 'exercise_content': markdown,
                'children': []}
        sections = [node]
        extract_exercises(sections)
        self.assertEqual(len(node['exercises']), 2)
        self.assertIn('graph-1.jpg', node['exercises'][0]['content'])
        self.assertNotIn('graph-2.jpg', node['exercises'][0]['content'])
        self.assertIn('graph-2.jpg', node['exercises'][1]['content'])

    def test_captioned_group_still_follows_its_reference(self):
        markdown = (
            '1. See 图 1-1.\n\n'
            '<div><img src="captioned.jpg" /></div>\n'
            '<div>图 1-1</div>\n\n'
            '2. Other question.\n'
        )
        cleaned, figures = extract_figures(markdown)
        self.assertNotIn('captioned.jpg', cleaned)
        self.assertIn('captioned.jpg', '\n'.join(figures['图1-1']))
        node = {'title': '习题1-1', 'has_exercise': True,
                'exercise_page_range': [1, 2], 'exercise_content': markdown,
                'children': []}
        extract_exercises([node])
        self.assertIn('captioned.jpg', node['exercises'][0]['content'])
        self.assertNotIn('captioned.jpg', node['exercises'][1]['content'])

    def test_later_caption_does_not_steal_previous_question_image(self):
        markdown = (
            '1. Read the first graph.\n\n'
            '<img src="first.jpg" />\n\n'
            '2. See 图 1-1.\n\n'
            '<img src="second.jpg" />\n'
            '图 1-1\n'
        )
        node = {'title': 'Exercises', 'has_exercise': True,
                'exercise_page_range': [1, 2], 'exercise_content': markdown,
                'children': []}
        extract_exercises([node])
        self.assertIn('first.jpg', node['exercises'][0]['content'])
        self.assertNotIn('second.jpg', node['exercises'][0]['content'])
        self.assertIn('second.jpg', node['exercises'][1]['content'])
