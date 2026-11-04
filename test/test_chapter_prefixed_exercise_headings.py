import unittest

from exercise_extractor.extraction import extract_exercises
from exercise_extractor.parse_md_exercises import truncate_to_section_heading


class ChapterPrefixedExerciseHeadingTests(unittest.TestCase):
    def test_truncation_accepts_chapter_prefix_and_case_difference(self):
        content = (
            '3. Answer to the previous section.\n\n'
            '## CHAPTER 2 SUPPLEMENTARY EXERCISES\n\n'
            '1. First supplementary question.\n'
        )
        self.assertEqual(
            truncate_to_section_heading(content, 'Supplementary Exercises'),
            '## CHAPTER 2 SUPPLEMENTARY EXERCISES\n\n1. First supplementary question.\n',
        )

    def test_explicit_section_does_not_include_previous_question(self):
        node = {
            'title': 'Supplementary Exercises',
            'has_exercise': True,
            'exercise_page_range': [179, 180],
            'exercise_content': (
                '<!-- page: 179 -->\n'
                '3. Answer to the previous section.\n\n'
                '## CHAPTER 2 SUPPLEMENTARY EXERCISES\n\n'
                '1. First supplementary question.\n\n'
                '2. Second supplementary question.\n'
            ),
            'children': [],
        }
        extract_exercises([node])
        self.assertEqual(len(node['exercises']), 2)
        self.assertIn('First supplementary question', node['exercises'][0]['content'])
        self.assertNotIn('previous section', node['exercises'][0]['content'])


if __name__ == '__main__':
    unittest.main()
