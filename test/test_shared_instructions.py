import unittest

from exercise_extractor import extract_exercise_from_markdown
from exercise_extractor.parse_md_exercises import split_exercises


class SharedInstructionTests(unittest.TestCase):
    def test_unnumbered_following_exercises_instructions_appear_only_before_first_question(self):
        exercises = split_exercises(
            '<!-- page: 29 -->\n'
            '### SECTION 1.1 EXERCISES\n\n'
            'For the following exercises, sketch the curves below by eliminating '
            'the parameter. Give the orientation of the curve.\n\n'
            '1. $x=t^2$\n\n'
            '2. $x=\\cos(t)$\n\n'
            'For the following exercises, eliminate the parameter and sketch '
            'the graphs.\n\n'
            '3. $x=2t$\n\n'
            '4. $x=3t$',
            keep_leading=False,
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises], ['1', '2', '3', '4'])
        self.assertTrue(exercises[0]['content'].startswith('For the following exercises, sketch'))
        self.assertNotIn('For the following exercises', exercises[1]['content'])
        self.assertTrue(exercises[2]['content'].startswith('For the following exercises, eliminate'))
        self.assertNotIn('For the following exercises', exercises[3]['content'])
        self.assertNotIn('eliminate the parameter and sketch the graphs', exercises[1]['content'])
        self.assertEqual([item['pages'] for item in exercises], [[29, 29]] * 4)

    def test_following_exercises_instruction_crosses_page_boundary(self):
        exercises = split_exercises(
            '1. Earlier question.\n\n'
            '<!-- page: 56 -->\n'
            'For the following exercises, use technology\n'
            'to sketch the parametric equations.\n\n'
            '<!-- page: 57 -->\n'
            '2. $x=t$\n\n'
            '3. $y=t^2$',
            check_section_end=False,
        )
        self.assertNotIn('use technology', exercises[0]['content'])
        self.assertTrue(exercises[1]['content'].startswith('For the following exercises, use technology'))
        self.assertIn('to sketch the parametric equations.', exercises[1]['content'])
        self.assertNotIn('use technology', exercises[2]['content'])
        self.assertEqual(exercises[1]['pages'], [56, 57])

    def test_following_exercises_without_next_question_stays_with_previous_question(self):
        exercises = split_exercises(
            '1. Discuss the phrase below.\n\n'
            'For the following exercises, no question follows.',
            check_section_end=False,
        )
        self.assertIn('For the following exercises', exercises[0]['content'])

    def test_public_markdown_keeps_unnumbered_instruction_once(self):
        sections = list(extract_exercise_from_markdown(
            '# Exercises\n\n'
            'For the following exercises, sketch the graphs.\n\n'
            '1. $x=t$\n\n'
            '2. $y=t^2$',
            title='Exercises',
        ))
        self.assertEqual(len(sections[0]['exercises']), 2)
        self.assertTrue(sections[0]['exercises'][0]['content'].startswith(
            'For the following exercises, sketch the graphs.'))
        self.assertNotIn('For the following exercises', sections[0]['exercises'][1]['content'])
        self.assertEqual(sections[0]['content']['original'].count(
            'For the following exercises, sketch the graphs.'), 1)

    def test_range_instruction_is_attached_to_each_numbered_question(self):
        exercises = split_exercises(
            '34. Earlier question.\n\n'
            '35–38 Evaluate the difference quotient. Simplify your answer.\n\n'
            '35. $f(x)=x^2$\n\n'
            '36. $f(x)=x^3$\n\n'
            '37. $f(x)=1/x$\n\n'
            '38. $f(x)=\\sqrt{x}$\n\n'
            '39. Next question.',
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises],
                         ['34', '35', '36', '37', '38', '39'])
        self.assertNotIn('35–38', exercises[0]['content'])
        for exercise in exercises[1:5]:
            self.assertIn('35–38 Evaluate the difference quotient.',
                          exercise['content'])
        self.assertNotIn('35–38', exercises[5]['content'])

    def test_instruction_survives_page_boundary(self):
        exercises = split_exercises(
            '<!-- page: 56 -->\n'
            '39–40 Find the domain.\n\n'
            '<!-- page: 57 -->\n'
            '39. $f(x)=1/x$\n\n'
            '40. $f(x)=\\sqrt{x}$',
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises], ['39', '40'])
        self.assertTrue(all('39–40 Find the domain.' in item['content']
                            for item in exercises))
        self.assertEqual([item['pages'] for item in exercises],
                         [[56, 57], [56, 57]])

    def test_unmatched_range_is_retained_for_review(self):
        exercises = split_exercises(
            '1. Earlier question.\n\n'
            '35–38 Evaluate the difference quotient.\n\n'
            '2. Another question.',
            check_section_end=False,
        )
        self.assertIn('35–38 Evaluate the difference quotient.',
                      exercises[0]['content'])

    def test_public_markdown_section_contains_each_instruction(self):
        sections = list(extract_exercise_from_markdown(
            '# Exercises\n\n'
            '35-36 Find the domain.\n\n'
            '35. $f(x)=1/x$\n\n'
            '36. $g(x)=\\sqrt{x}$',
            title='Exercises',
        ))
        self.assertEqual(len(sections), 1)
        self.assertEqual(len(sections[0]['exercises']), 2)
        self.assertTrue(all('35-36 Find the domain.' in item['content']
                            for item in sections[0]['exercises']))
        self.assertEqual(sections[0]['content']['original'].count(
            '35-36 Find the domain.'), 2)

    def test_lay_style_in_exercises_range(self):
        exercises = split_exercises(
            'In Exercises 3–4, find the steady-state vector.\n\n'
            '3. $A_3$\n\n'
            '4. $A_4$\n\n'
            '5. Next question.',
            check_section_end=False,
        )
        self.assertTrue(all('In Exercises 3–4, find the steady-state vector.'
                            in item['content'] for item in exercises[:2]))
        self.assertNotIn('In Exercises', exercises[2]['content'])

    def test_lay_style_in_exercises_pair(self):
        exercises = split_exercises(
            'In Exercises 9 and 10, find the change-of-coordinates matrix.\n\n'
            '9. $B_9$\n\n'
            '10. $B_{10}$\n\n'
            '11. Next question.',
            check_section_end=False,
        )
        self.assertTrue(all('In Exercises 9 and 10,' in item['content']
                            for item in exercises[:2]))
        self.assertNotIn('In Exercises', exercises[2]['content'])

    def test_shared_subquestions_before_numbered_figures(self):
        exercises = split_exercises(
            '1–2 Use the given graph to find the following.\n\n'
            '(a) Where is the function increasing?\n\n'
            '(b) Where is it decreasing?\n\n'
            '1.\n\n<img src="one.jpg" />\n\n'
            '<div style="text-align: center;">2.</div>\n\n'
            '<img src="two.jpg" />\n\n'
            '3. Next question.',
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises], ['1', '2', '3'])
        for item in exercises[:2]:
            self.assertIn('1–2 Use the given graph', item['content'])
            self.assertIn('(a) Where is the function increasing?', item['content'])
            self.assertIn('(b) Where is it decreasing?', item['content'])
        self.assertIn('one.jpg', exercises[0]['content'])
        self.assertNotIn('two.jpg', exercises[0]['content'])
        self.assertIn('two.jpg', exercises[1]['content'])
        self.assertNotIn('1–2', exercises[2]['content'])

    def test_missing_number_between_two_image_only_questions(self):
        exercises = split_exercises(
            '4. Previous question.\n\n'
            '5–6 The derivative graph is shown.\n\n'
            '(a) Where is f increasing?\n\n'
            '(b) Where is f decreasing?\n\n'
            '5.\n\n<img src="five.jpg" />\n\n'
            '<img src="six.jpg" />\n\n'
            '7. Next question.',
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises],
                         ['4', '5', '6', '7'])
        for item in exercises[1:3]:
            self.assertIn('5–6 The derivative graph is shown.', item['content'])
            self.assertIn('(b) Where is f decreasing?', item['content'])
        self.assertIn('five.jpg', exercises[1]['content'])
        self.assertNotIn('six.jpg', exercises[1]['content'])
        self.assertIn('six.jpg', exercises[2]['content'])

    def test_two_figures_in_one_question_do_not_invent_another_question(self):
        exercises = split_exercises(
            '5–6 Compare the graphs.\n\n'
            '5.\n\n<img src="first.jpg" />\n\n'
            '<img src="second.jpg" />\n\n'
            '6. Discuss both figures.\n\n'
            '7. Next question.',
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises], ['5', '6', '7'])
        self.assertIn('second.jpg', exercises[0]['content'])

    def test_public_section_splits_graph_pairs_and_keeps_shared_prompts(self):
        sections = list(extract_exercise_from_markdown(
            '# Exercises\n\n'
            '1–2 Read the graph.\n\n'
            '(a) Find the intervals.\n\n'
            '1.\n\n<img src="one.jpg" />\n\n'
            '<div>2.</div>\n\n<img src="two.jpg" />\n\n'
            '3. Other question.\n\n'
            '5–6 Read the derivative graphs.\n\n'
            '(a) Find the extrema.\n\n'
            '5.\n\n<img src="five.jpg" />\n\n'
            '<img src="six.jpg" />\n\n'
            '7. Next question.',
            title='Exercises',
        ))
        self.assertEqual(len(sections), 1)
        contents = [item['content'] for item in sections[0]['exercises']]
        self.assertEqual(len(contents), 6)
        self.assertIn('one.jpg', contents[0])
        self.assertIn('two.jpg', contents[1])
        self.assertIn('five.jpg', contents[3])
        self.assertIn('six.jpg', contents[4])
        self.assertTrue(all('1–2 Read the graph.' in text for text in contents[:2]))
        self.assertTrue(all('5–6 Read the derivative graphs.' in text
                            for text in contents[3:5]))

    def test_range_only_line_with_following_shared_subquestions(self):
        exercises = split_exercises(
            '18. Previous question.\n\n'
            '19–20\n\n'
            '(a) Find where f increases.\n\n'
            '(b) Find its extrema.\n\n'
            '19. $f(x)=x^2$\n\n'
            '20. $f(x)=x^3$\n\n'
            '21. Next question.',
            check_section_end=False,
        )
        self.assertNotIn('19–20', exercises[0]['content'])
        self.assertTrue(all('19–20' in item['content']
                            and '(a) Find where f increases.' in item['content']
                            for item in exercises[1:3]))
        self.assertNotIn('19–20', exercises[3]['content'])

    def test_missing_first_number_after_shared_graph(self):
        exercises = split_exercises(
            '5. Earlier question.\n\n<img src="five.jpg" />\n\n'
            '6–7 The graph of y is given. Use transformations.\n\n'
            '<img src="shared.jpg" />\n\n'
            '<img src="six.jpg" />\n\n'
            '<div>7.</div>\n\n'
            '<img src="seven.jpg" />\n\n'
            '8. Next question.',
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises],
                         ['5', '6', '7', '8'])
        self.assertEqual([item['content'].count('<img') for item in exercises],
                         [1, 2, 2, 0])
        self.assertIn('shared.jpg', exercises[1]['content'])
        self.assertIn('six.jpg', exercises[1]['content'])
        self.assertIn('shared.jpg', exercises[2]['content'])
        self.assertIn('seven.jpg', exercises[2]['content'])
        self.assertNotIn('6–7', exercises[0]['content'])

    def test_ambiguous_shared_graph_stays_in_source_text(self):
        exercises = split_exercises(
            '5. Earlier question.\n\n'
            '6–7 Use the graphs.\n\n'
            '<img src="only-one.jpg" />\n\n'
            '<div>7.</div>\n\n'
            '<img src="seven.jpg" />',
            check_section_end=False,
        )
        self.assertEqual([item['number'] for item in exercises], ['5', '7'])
        self.assertIn('6–7 Use the graphs.', exercises[0]['content'])

    def test_public_section_preserves_shared_and_individual_graphs(self):
        sections = list(extract_exercise_from_markdown(
            '# Exercises\n\n'
            '5. Earlier question.\n\n<img src="five.jpg" />\n\n'
            '6–7 Use the shared graph to answer each question.\n\n'
            '<img src="shared.jpg" />\n\n'
            '<img src="six.jpg" />\n\n'
            '<div>7.</div>\n\n<img src="seven.jpg" />',
            title='Exercises',
        ))
        self.assertEqual(len(sections), 1)
        exercises = sections[0]['exercises']
        self.assertEqual(len(exercises), 3)
        self.assertIn('shared.jpg', exercises[1]['content'])
        self.assertIn('six.jpg', exercises[1]['content'])
        self.assertIn('shared.jpg', exercises[2]['content'])
        self.assertIn('seven.jpg', exercises[2]['content'])
        self.assertEqual(sections[0]['content']['original'].count('shared.jpg'), 2)


if __name__ == '__main__':
    unittest.main()
