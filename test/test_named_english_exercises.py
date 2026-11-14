import unittest
import tempfile
from pathlib import Path

from exercise_extractor.parse_md_exercises import split_exercises
from exercise_extractor.extraction import combine_section_pages, extract_exercises


class NamedEnglishExerciseTests(unittest.TestCase):
    def test_labels_with_heading_bold_and_wrapped_question(self):
        content = ('## 1.5 Exercises\n\nExercise 1.5.1\nFind the probability.\n'
                   '1. First part.\n2. Second part.\n'
                   '### Exercise 1.5.2\nShow independence.\n'
                   '**Exercise 1.5.3** Prove the identity.\n')
        items = split_exercises(content)
        self.assertEqual([e['number'] for e in items], ['1.5.1', '1.5.2', '1.5.3'])
        self.assertIn('2. Second part.', items[0]['content'])
        self.assertIn('Prove the identity.', items[2]['content'])

    def test_textbook_pipeline_keeps_exercises_and_stops_at_next_section(self):
        nodes = [{'title': '1.5 Exercises', 'has_exercise': True, 'children': [],
                  'boundary_title': '2 Basic Probability Laws',
                  'exercise_page_range': [1, 3]}]
        with tempfile.TemporaryDirectory() as tmp:
            pages = ['## 1.5 Exercises\nExercise 1.5.1\nFirst question.\n',
                     'Exercise 1.5.2\nSecond question.\n## 2 Basic Probability Laws\n'
                     'Example 2.1\nNot an exercise.']
            for number, content in enumerate(pages, 1):
                folder = Path(tmp) / f'page_{number}'
                folder.mkdir()
                (folder / 'page.md').write_text(content)
                (folder / 'page.json').write_text('{"parsing_res_list": []}')
                (folder / 'READY').write_text('ready\n')
            combine_section_pages(nodes, tmp)
            extract_exercises(nodes)
        self.assertEqual([e['original_number'] for e in nodes[0]['exercises']], ['1.5.1', '1.5.2'])
        self.assertNotIn('Not an exercise', nodes[0]['exercises'][-1]['content'])
        self.assertEqual(nodes[0]['exercises'][1]['pages'], [2, 2])

    def test_explicit_label_after_solution_starts_next_exercise(self):
        items = split_exercises('Exercise 2.1 First question.\n解：\n1. First step.\n'
                                '2. Second step.\nExercise 2.2 Next question.')
        self.assertEqual([e['number'] for e in items], ['2.1', '2.2'])
        self.assertIn('2. Second step.', items[0]['solution_steps'])


if __name__ == '__main__':
    unittest.main()
