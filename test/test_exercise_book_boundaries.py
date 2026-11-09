import unittest

from exercise_extractor.exercise_book import build_exercise_book_sections, extract_exercises


class ExerciseBookBoundaryTests(unittest.TestCase):
    def test_out_of_order_boundary_does_not_extend_to_end_of_book(self):
        tree = [{'title': 'Chapter', 'children': [
            {'title': '1.1', 'page_num': 127},
            {'title': '1.2', 'page_num': 32},
        ]}]
        sections = build_exercise_book_sections(tree, 758)
        self.assertEqual(sections[0]['children'][0]['exercise_page_range'], [127, 128])

    def test_same_page_boundary_is_retained(self):
        tree = [{'title': 'Chapter', 'children': [
            {'title': '1.1', 'page_num': 11},
            {'title': '1.2', 'page_num': 11},
        ]}]
        first = build_exercise_book_sections(tree, 758)[0]['children'][0]
        self.assertEqual(first['exercise_page_range'], [11, 12])
        self.assertEqual(first['boundary_title'], '1.2')

    def test_boundary_page_heading_excludes_the_next_section(self):
        tree = [{'title': 'Chapter', 'children': [
            {'title': '1.1 First', 'page_num': 11},
            {'title': '1.2 Second', 'page_num': 32},
        ]}]
        first = build_exercise_book_sections(tree, 758)[0]['children'][0]
        first['exercise_content'] = (
            '<!-- page: 11 -->\n# 1.1 First\n\n1. First section problem.\n'
            '<!-- page: 32 -->\n# 1.2 Second\n\n2. Next section problem.\n'
        )
        extract_exercises([first])
        self.assertEqual(len(first['exercises']), 1)
        self.assertIn('First section problem', first['exercises'][0]['content'])
        self.assertNotIn('Next section problem', str(first['exercises']))


if __name__ == '__main__':
    unittest.main()
